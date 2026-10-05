"""Export planted cases as training records that carry the LOGIC, not only the fix.

    python3 export_rationale.py --cases cases.jsonl --split dev   -> E/rationale_<split>.jsonl

record = {input: function + oracle diff; rationale: the catalog rules solver.principles.residual_rules names for
this residual (rule, what was observed, what it means); edit: the line-level change that is verified byte-exact}.
The rationale is derived mechanically from the binary's diff and the tested catalog -- never written by a model --
so a model trained on it learns rule -> edit, and an unexplained residual carries an EMPTY rationale rather than an
invented one. Refuses the frozen held-out set: training must be planted on functions neither dev nor held-out uses.
"""
import argparse
import difflib
import json

import run
from patterns.catalog import CATALOG
from solver import principles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases.jsonl")
    ap.add_argument("--split", default="dev")
    a = ap.parse_args()
    assert "heldout" not in a.cases, "the frozen held-out set is never exported for training"
    out = run.E / f"rationale_{a.split}.jsonl"
    n = explained = 0
    with open(out, "w") as f:
        for c in map(json.loads, open(run.E / a.cases)):
            rules = principles.residual_rules(c["diff"])
            edit = [l for l in difflib.unified_diff(c["perturbed_def"].split("\n"), c["original_def"].split("\n"),
                                                    lineterm="", n=0)][2:]
            record = {
                "id": c["id"], "split": a.split, "function": c["function"], "planted_class": c["class"],
                "input": {"function": c["perturbed_def"], "diff": c["diff"]},
                "rationale": [{"rule": pid, "name": CATALOG[pid].name, "observed": reason, "means": CATALOG[pid].means}
                              for pid, reason in rules],
                "edit": edit,
                "verified": "the edited function compiles byte-exact (masked dump equals the target); re-certify with "
                            "solver.byte_certificate before training use",
            }
            f.write(json.dumps(record) + "\n")
            n += 1
            explained += bool(rules)
    print(f"{n} records -> {out}; {explained} with a rule-based rationale, {n - explained} with an empty one")


if __name__ == "__main__":
    main()

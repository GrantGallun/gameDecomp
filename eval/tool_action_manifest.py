"""Combine the two dataset sources into one training file and report the balance.

The sources stay distinguishable in the output (`kind`, `label_source`) because spec §5 requires
separate metrics and explicit sampling weights for procedural and outcome-backed examples: a single
blended accuracy number would let the cheap mechanical exercises stand in for the expensive
execution-backed ones.

BALANCE IS DONE BY WEIGHTING, NOT BY DELETION. Every record survives; under-represented action
families are repeated a deterministic number of times and over-represented ones once, so the loss
sees each family with roughly equal frequency without any evidence being thrown away. The weights are
written into the manifest as counts so a reader can reproduce the composition exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


def read(path: Path, split: str) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("split") == split:
                rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", type=Path, nargs="+", required=True)
    ap.add_argument("--split", default="train")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--max-per-family", type=int, default=3,
                    help="cap on how many times one action family is repeated")
    ap.add_argument("--max-alternatives", type=int, default=3,
                    help="how many acceptable actions of one state become training targets")
    args = ap.parse_args(argv)

    rows = [row for path in args.inputs for row in read(path, args.split)]

    # ONE TARGET PER ACCEPTABLE ALTERNATIVE. State labels that admit several actions become several
    # examples rather than one canonical answer with the rest discarded (spec §3: "Include multiple
    # valid next actions with balanced weighting", §5: "Compare all acceptable verified actions
    # rather than penalizing a valid alternative merely for differing from the recorded child"). It is
    # also what removes the skew: most late states accept several transforms, whereas taking only the
    # first acceptable action made `redraft` 65% of the set simply because the cheaper transforms had
    # already been tried and excluded.
    #
    # `stop` IS NEVER ADDED BY EXPANSION. It appears in an acceptable set only when nothing else is
    # left, and turning that trailing option into a training target of its own would teach stopping
    # early -- the one behaviour the honest-stop examples exist to contrast against.
    expanded: list[dict] = []
    for row in rows:
        options = [item["action"] for item in row["acceptable"]]
        if not options:
            continue
        alternatives = options if len(options) == 1 else [
            name for name in options if name != "stop"][:args.max_alternatives]
        for position, name in enumerate(alternatives or options):
            clone = dict(row)
            clone["action"] = {"action": name, "params": {}}
            clone["completion"] = json.dumps(clone["action"], sort_keys=True)
            if position:
                clone["id"] = f"{row['id']}#alt{position}"
            expanded.append(clone)

    families = Counter(row["action"]["action"] for row in expanded)
    target = max(families.values()) if families else 1
    weighted: list[dict] = []
    weights: dict[str, int] = {}
    for row in expanded:
        action = row["action"]["action"]
        # Deterministic and bounded: repeat an under-represented family up to `max_per_family` times
        # and never below once. `stop` is deliberately NOT boosted -- an action that is correct only
        # when the evidence says so should not be over-sampled into a habit.
        weight = 1 if action == "stop" else min(args.max_per_family,
                                                max(1, round(target / max(1, families[action]))))
        weights[action] = weight
        for copy in range(weight):
            clone = dict(row)
            clone["id"] = f"{row['id']}~w{copy}" if weight > 1 else row["id"]
            weighted.append(clone)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in weighted:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    manifest = {
        "split": args.split,
        "inputs": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "records": len(read(path, args.split))} for path in args.inputs],
        "records": len(rows), "expanded": len(expanded), "emitted": len(weighted),
        "action_counts": dict(sorted(families.items())),
        "source_action_counts": dict(sorted(Counter(r["action"]["action"] for r in rows).items())),
        "field_counts": {
            "kind": dict(sorted(Counter(r["kind"] for r in rows).items())),
            "label_source": dict(sorted(Counter(r["label_source"] for r in rows).items())),
            "label_confidence": dict(sorted(Counter(r["label_confidence"] for r in rows).items())),
            "functions": len({r["function"] for r in rows}),
            "families": len({r["family"] for r in rows}),
        },
        "weights": dict(sorted(weights.items())),
        "out": str(args.out),
        "out_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "note": ("procedural and outcome records are both present and stay distinguishable by `kind`; "
                 "the evaluation reports them separately"),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

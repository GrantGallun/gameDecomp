"""Logged causal probe: generated C only; no reference implementation bodies."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

CODE = Path.home() / "decomp/experiments/search-evolution-20260922/code-diagnosis-v1"
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate
from eval.search_evolution import compile_logged
from solver import compiler_recipe

SHARED = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/register-storage-20260922/probe"
REPO = Path.home() / "decomp/sbk1"
NAME = "__osDequeueThread"


def main():
    NATIVE.mkdir(parents=True, exist_ok=False)
    original = json.loads((SHARED.parent / "search-evolution-20260922/diagnosis-v1" /
                         f"followup--{NAME}--breadth.world.json").read_text())["world"]["nodes"][0]["source"]
    pointer = original.replace("    OSThread **var_a2;", "    register OSThread **var_a2;")
    value = original.replace("    OSThread *var_a3;", "    register OSThread *var_a3;")
    both = pointer.replace("    OSThread *var_a3;", "    register OSThread *var_a3;")
    # Natural loop control tests whether cleaning the goto alone fixes spilling.
    start = original.index("    var_a2 = queue;")
    cleaned = original[:start] + """    var_a2 = queue;
    var_a3 = *var_a2;
    while (var_a3 != NULL) {
        if (var_a3 == t) {
            *var_a2 = t->next;
            return;
        }
        var_a2 = &var_a3->next;
        var_a3 = *var_a2;
    }
}
"""
    db = sqlite3.connect(NATIVE / "attempts.sqlite")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()
    repo = isolate(REPO, NATIVE / "workspace", NAME)
    ws = repo / "nonmatchings" / NAME
    target = json.loads((ws / ".compiler-target.json").read_text())["target"]
    recipe = compiler_recipe.resolve(repo, target)
    results, exacts = [], []
    for label, source in [("baseline", original), ("natural-loop", cleaned),
                          ("register-pointer", pointer), ("register-value", value), ("register-both", both)]:
        verdict = compile_logged(ws, repo, NAME, source, conn=db,
            strategy=f"register-storage-probe:{label}", run_id="register-storage-probe",
            parent_attempt_id=results[0]["receipt_id"] if results else None,
            action=label, model="deterministic-probe", prompt="Generated draft and target compiler evidence only.",
            extra={"training_eligible": False, "assistance_tier": "header-assisted"})
        assert not verdict.get("error"), verdict
        (SHARED / f"probe-{label}.c").write_text(source)
        (SHARED / f"probe-{label}.json").write_text(json.dumps(verdict, indent=2))
        results.append({"label": label, "source_sha256": hashlib.sha256(source.encode()).hexdigest(), **verdict})
        if verdict["exact"]:
            exacts.append((label, source, verdict["receipt_id"]))
        print(json.dumps({"label": label, "score": verdict["score"], "exact": verdict["exact"]}), flush=True)
    for label, source, parent in exacts:
        confirm_repo = isolate(REPO, NATIVE / f"confirm-{label}", NAME)
        verdict = compile_logged(confirm_repo / "nonmatchings" / NAME, confirm_repo, NAME, source, conn=db,
            strategy="register-storage-probe:confirmation", run_id="register-storage-probe-confirm",
            parent_attempt_id=parent, action="independent-confirmation", model="deterministic-probe",
            extra={"training_eligible": False, "assistance_tier": "header-assisted"})
        assert verdict["exact"] and not verdict.get("error")
        results.append({"label": f"confirm-{label}", **verdict})
    report = {"kind": "causal-development-probe", "training_eligible": False,
              "database": str(NATIVE / "attempts.sqlite"), "recipe": recipe,
              "runs": results, "total_compiles": len(results)}
    (SHARED / "probe.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

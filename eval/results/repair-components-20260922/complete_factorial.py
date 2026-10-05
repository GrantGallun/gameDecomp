"""Two missing cells of a three-component, single-function experiment.

This is an isolated analysis probe, not a deployed repair or learned policy.
Existing source-bound observations fill six cells; only missing cells compile.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / "register-storage-20260922"
FROZEN = json.loads((PRIOR / "freeze-v3.json").read_text())
CODE = Path(FROZEN["code_root"])
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate
from eval.search_evolution import compile_logged
sys.path.append(str(CODE / "eval/results/dream-search-20260922"))
from pilot import compiler_identity

NATIVE = Path.home() / "decomp/experiments/repair-components-20260922"
REPO = Path.home() / "decomp/sbk1"
NAME = "__osDequeueThread"


def main():
    assert not (OUT / "completion.json").exists()
    NATIVE.mkdir(parents=True, exist_ok=True)
    prior = json.loads((PRIOR / "probe.json").read_text())
    old = sqlite3.connect(f"file:{prior['database']}?mode=ro", uri=True)
    fresh = not (NATIVE / "attempts.sqlite").exists()
    db = sqlite3.connect(NATIVE / "attempts.sqlite")
    if fresh:
        old.backup(db)
    db.row_factory = sqlite3.Row
    inherited = old.execute("SELECT count(*) FROM attempts").fetchone()[0]
    preregistration = {
        "question": "Which register and address-reuse components interact on the frozen pointer-walk residual?",
        "function": NAME, "factors": ["P: register pointer-to-link", "N: register node pointer", "A: reuse field address"],
        "missing_cells": [[1,0,1], [0,1,1]], "new_compile_cap": 4,
        "outcomes": ["normalized similarity (diagnostic)", "certificate exactness"],
        "inference_scope": "single exposed development function; descriptive component decomposition, no population inference",
        "training_eligible": False, "production_mutated": False, "model_calls": 0,
        "source_database": prior["database"], "database": str(NATIVE / "attempts.sqlite"),
        "inherited_attempts": inherited, "code_root": str(CODE), "fixed_files": FROZEN["files"],
    }
    if not (OUT / "preregistration.json").exists():
        (OUT / "preregistration.json").write_text(json.dumps(preregistration, indent=2))
    original_repo = Path(prior["database"]).parent / "workspace"
    original_identity = compiler_identity(original_repo, original_repo / "nonmatchings" / NAME)
    rows, confirmations = [], []
    for label, factors in (("register-pointer", [1,0,1]), ("register-value", [0,1,1])):
        parent = next(r for r in prior["runs"] if r["label"] == label)
        original = (PRIOR / f"probe-{label}.c").read_text()
        needle = "        var_a3 = var_a3->next;"
        assert original.count(needle) == 1
        source = original.replace(needle, "        var_a3 = *var_a2;")
        cell = label + "-alias"
        (OUT / f"{cell}.c").write_text(source)
        recovered = db.execute("SELECT * FROM attempts WHERE strategy=? AND source_sha256=?",
            ("repair-component-factorial", hashlib.sha256(source.encode()).hexdigest())).fetchall()
        assert len(recovered) <= 1
        repo = NATIVE / cell if recovered else isolate(REPO, NATIVE / cell, NAME)
        assert compiler_identity(repo, repo / "nonmatchings" / NAME) == original_identity
        for path, sha in FROZEN["files"].items():
            assert hashlib.sha256((CODE / path).read_bytes()).hexdigest() == sha
        if recovered:
            stored = recovered[0]
            meta = json.loads(stored["sampling"])
            assert stored["source_code"] == source and stored["parent_attempt_id"] == parent["receipt_id"]
            verdict = {"compiled": bool(stored["compiled"]), "exact": bool(stored["exact"]),
                "score": stored["score"], "diff": stored["diff_summary"], "stderr": stored["compiler_stderr"],
                "receipt_id": stored["id"], **{k:meta.get(k) for k in ("verification", "frontend", "compiler_recipe")}}
        else:
            verdict = compile_logged(repo / "nonmatchings" / NAME, repo, NAME, source, conn=db,
                strategy="repair-component-factorial", run_id="repair-components-20260922",
                parent_attempt_id=parent["receipt_id"], action=cell, model="deterministic-probe",
                extra={"training_eligible": False, "assistance_tier": "header-assisted", "component_bits": factors})
        assert not verdict.get("error")
        assert verdict["compiled"]
        assert compiler_identity(repo, repo / "nonmatchings" / NAME) == original_identity
        assert verdict["compiler_recipe"]["projection_sha256"] == parent["compiler_recipe"]["projection_sha256"]
        rows.append({"label": cell, "factors": factors, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                     "parent_receipt_id": parent["receipt_id"], "compiler_identity": original_identity,
                     "recovered_from_durable_receipt": bool(recovered), **verdict})
        if verdict["exact"]:
            confirm = isolate(REPO, NATIVE / ("confirm-" + cell), NAME)
            result = compile_logged(confirm / "nonmatchings" / NAME, confirm, NAME, source, conn=db,
                strategy="repair-components-confirmation", run_id="repair-components-20260922",
                parent_attempt_id=verdict["receipt_id"], action="independent-confirmation", model="deterministic-probe",
                extra={"training_eligible": False, "assistance_tier": "header-assisted"})
            assert result["exact"] and not result.get("error")
            confirmations.append({"label": cell, **result})
        (OUT / "new-probes.json").write_text(json.dumps({"runs": rows, "confirmations": confirmations}, indent=2))
        print(json.dumps({"label": cell, "score": verdict["score"], "exact": verdict["exact"]}), flush=True)
    count = db.execute("SELECT count(*) FROM attempts").fetchone()[0] - inherited
    assert count == len(rows) + len(confirmations) <= preregistration["new_compile_cap"]
    (OUT / "completion.json").write_text(json.dumps({"complete": True, "new_compiles": count,
        "inherited_attempts": inherited, "database": str(NATIVE / "attempts.sqlite")}, indent=2))


if __name__ == "__main__":
    main()

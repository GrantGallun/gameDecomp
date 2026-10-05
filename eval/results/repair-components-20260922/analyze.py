"""Descriptive factorial decomposition with source and receipt validation.

No fitted population predictor, significance test, training or policy promotion.
The eight-point polynomial interpolates only this exposed development case.
"""
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / "register-storage-20260922"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def coefficients(values):
    # 0/1 treatment coding: the coefficient of a product is its finite difference
    # at the all-zero reference. These are not +/-1-coded average main effects.
    return {mask: sum(((-1) ** (mask.bit_count() - sub.bit_count())) * values[sub]
                     for sub in values if sub & mask == sub) for mask in values}


def main():
    completion = json.loads((OUT / "completion.json").read_text())
    assert completion["complete"] and completion["new_compiles"] == 2
    db = sqlite3.connect(f"file:{completion['database']}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM attempts")}
    prior = json.loads((PRIOR / "probe.json").read_text())["runs"]
    alias = json.loads((PRIOR / "probe-alias.json").read_text())
    added = json.loads((OUT / "new-probes.json").read_text())
    cases = {
        0: (prior, "baseline", PRIOR / "probe-baseline.c"),
        1: (prior, "register-pointer", PRIOR / "probe-register-pointer.c"),
        2: (prior, "register-value", PRIOR / "probe-register-value.c"),
        3: (prior, "register-both", PRIOR / "probe-register-both.c"),
        4: (alias, "baseline-alias", PRIOR / "probe-baseline-alias.c"),
        5: (added["runs"], "register-pointer-alias", OUT / "register-pointer-alias.c"),
        6: (added["runs"], "register-value-alias", OUT / "register-value-alias.c"),
        7: (alias, "register-both-alias", PRIOR / "probe-register-both-alias.c"),
    }
    baseline = (PRIOR / "probe-baseline.c").read_text()
    rows = []
    for mask, (records, label, path) in cases.items():
        row = next(r for r in records if r["label"] == label)
        stored = attempts[row["receipt_id"]]
        meta = json.loads(stored["sampling"])
        source = path.read_text()
        expected = baseline
        for bit, old, new in (
            (1, "    OSThread **var_a2;", "    register OSThread **var_a2;"),
            (2, "    OSThread *var_a3;", "    register OSThread *var_a3;"),
            (4, "        var_a3 = var_a3->next;", "        var_a3 = *var_a2;"),
        ):
            assert expected.count(old) == 1
            if mask & bit:
                expected = expected.replace(old, new)
        assert source == expected == stored["source_code"]
        assert digest(source) == stored["source_sha256"]
        assert row["compiled"] == bool(stored["compiled"]) and row["exact"] == bool(stored["exact"])
        assert row["score"] == stored["score"]
        assert row["diff"] == stored["diff_summary"]
        assert row.get("verification") == meta.get("verification")
        assert row["compiler_recipe"]["projection_sha256"] == meta["compiler_recipe"]["projection_sha256"]
        assert not meta["training_eligible"] and stored["compiled"]
        if stored["exact"]:
            cert = meta["verification"]
            assert cert["exact"] and cert["frontend"]["passed"]
            assert cert["candidate_source_sha256"] == digest(source)
        rows.append({"mask": mask, "P": int(bool(mask & 1)), "N": int(bool(mask & 2)), "A": int(bool(mask & 4)),
            "score": stored["score"], "exact": bool(stored["exact"]), "receipt_id": stored["id"],
            "source_sha256": digest(source), "source_file": str(path), "parent_receipt_id": stored["parent_attempt_id"],
            "compiler_recipe_sha256": meta["compiler_recipe"]["projection_sha256"]})
    assert len({r["compiler_recipe_sha256"] for r in rows}) == 1
    assert len(attempts) == completion["inherited_attempts"] + completion["new_compiles"]
    edges = {(r[0],r[1]) for r in db.execute("SELECT parent_attempt_id,child_attempt_id FROM attempt_edges")}
    for row in attempts.values():
        assert digest(row["source_code"]) == row["source_sha256"]
        if row["parent_attempt_id"] is not None:
            assert (row["parent_attempt_id"], row["id"]) in edges
            assert attempts[row["parent_attempt_id"]]["func_addr"] == row["func_addr"]
    values = {r["mask"]: Decimal(str(r["score"])) for r in rows}
    exacts = {r["mask"]: Decimal(int(r["exact"])) for r in rows}
    models = {}
    terms = {0:"intercept", 1:"P", 2:"N", 3:"P*N", 4:"A", 5:"P*A", 6:"N*A", 7:"P*N*A"}
    for name, outcome in (("diagnostic_score", values), ("observed_exact_indicator", exacts)):
        beta = coefficients(outcome)
        for mask in outcome:
            assert sum(value for sub,value in beta.items() if sub & mask == sub) == outcome[mask]
        models[name] = {terms[mask]: float(value) for mask,value in beta.items()}
    conditional = []
    for bit, name in ((1,"P"), (2,"N"), (4,"A")):
        for mask in values:
            if mask & bit:
                continue
            conditional.append({"component": name, "from_mask": mask, "to_mask": mask|bit,
                "score_effect": float(values[mask|bit] - values[mask]),
                "exact_effect": int(exacts[mask|bit] - exacts[mask])})
    report = {"complete": True, "function": "__osDequeueThread", "new_compiles": 2,
        "reused_factorial_cells": 6, "independent_function_contexts": 1, "factorial_cells": rows,
        "coding": "0/1; coefficients relative to all components off",
        "models": models, "conditional_effects": conditional,
        "residual_degrees_of_freedom": 0, "p_values": None, "confidence_intervals": None,
        "scope": "saturated descriptive polynomial on one exposed function, not a general probability model",
        "all_receipt_bindings_valid": True, "all_sources_differ_only_by_declared_components": True,
        "training_eligible": False, "production_mutated": False}
    (OUT / "analysis.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("new_compiles", "independent_function_contexts", "models", "conditional_effects")}, indent=2))


if __name__ == "__main__":
    main()

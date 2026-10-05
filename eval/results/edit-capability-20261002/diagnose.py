"""Diagnosis accuracy: which catalog rules solver.principles.residual_rules names for each DEVELOPMENT case.

Expected rule per planted class (None = no 2026-10-02 rule describes it; anything named there is a false positive).
"""
import collections
import json

import run
from solver import principles

EXPECTED = {
    "commute": "ido53-commutative-operand-materialisation-order",
    "stmt_swap": "ido53-adjacent-store-order-is-preserved",
    "temp_return": "ido53-o1-result-temporary-has-a-stack-home",
    "drop_stmt": "target-only-store-is-a-missing-statement",
    "decl_width": ("candidate-only-extension-widens-a-declaration", "s16-sign-extend",
                   "load-opcode-names-the-access-type"),
    "cast_width": ("candidate-only-extension-widens-a-declaration", "s16-sign-extend"),
    "if_invert": None, "const": None, "arith_op": None, "arg_swap": None,
}

table = collections.defaultdict(collections.Counter)
rows = []
for c in map(json.loads, open(run.E / "cases.jsonl")):
    named = [pid for pid, _r in principles.residual_rules(c["diff"])]
    want = EXPECTED[c["class"]]
    wants = set(want) if isinstance(want, tuple) else {want} if want else set()
    t = table[c["class"]]
    t["n"] += 1
    t["right"] += bool(wants & set(named))
    t["other"] += len([p for p in named if p not in wants])
    t["silent"] += not named
    rows.append({"id": c["id"], "named": named})
print(f"{'class':12} {'n':>3} {'expected rule named':>20} {'other rules named':>18} {'nothing named':>14}")
for cls, t in table.items():
    exp = f"{t['right']}/{t['n']}" if EXPECTED[cls] else "(none expected)"
    print(f"{cls:12} {t['n']:>3} {exp:>20} {t['other']:>18} {t['silent']:>14}")
(run.HERE / "diagnosis_dev.json").write_text(json.dumps(rows, indent=1))

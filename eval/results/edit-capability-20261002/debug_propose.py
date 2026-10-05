"""Show site_edits proposals for one DEVELOPMENT case (never a held-out one)."""
import json
import sys

import run
from localize import build_attr
from solver import site_edits

cid = sys.argv[1]
assert not cid.startswith("heldout")
case = next(c for c in map(json.loads, open(run.E / "cases.jsonl")) if c["id"] == cid)
code = case["head"] + case["perturbed_def"] + case["tail"]
b = build_attr(case["function"], "ec_dbg", code)
edits, receipt = site_edits.propose(code, case["function"], b["diff"], b["attr"], mined=True, operators=True, gaps=True)
print(json.dumps({k: v for k, v in receipt.items()}, default=str)[:600])
head_lines = case["head"].count("\n")
print("site lines (def-relative):", {k - head_lines: v for k, v in receipt.get("sites", {}).items()})
for i, e in enumerate(edits[:40]):
    print(i, e.kind, "|", e.label[:90], "| line", e.line - head_lines)
print("total", len(edits))

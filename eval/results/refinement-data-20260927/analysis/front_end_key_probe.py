"""Measure a stage key before anything relies on it. Read-only, pilot KB + pilot workspaces.

For compiled branch-point alternatives (parent, child, and whether the child's object equalled the
parent's), compute solver.ido_stages.<stage>_key for both. The claim to test:
  same key  =>  same object          (precision must be ~100% to skip compiles)
and how many real no-ops the key catches (recall).

    python3 front_end_key_probe.py N {front-end|optimizer}
"""
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import ido_stages  # noqa: E402
from solver.branch_points import diff_body  # noqa: E402

NATIVE = Path("/home/grant/decomp/experiments/redraft-pilot-20260927")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 400
STAGE = sys.argv[2] if len(sys.argv) > 2 else "front-end"
KEY = {"front-end": ido_stages.front_end_key, "optimizer": ido_stages.optimizer_key}[STAGE]
db = sqlite3.connect(f"file:{NATIVE / 'pilot.sqlite'}?mode=ro", uri=True)
rows = db.execute(
    "select c.id, c.source_code, c.diff_summary, p.source_code, p.diff_summary, p.id, "
    "(select name from functions where addr=c.func_addr) from attempts c join attempts p "
    "on p.id=c.parent_attempt_id where c.strategy='model-branch-point' and c.compiled=1 "
    "and p.compiled=1 order by c.id").fetchall()[:LIMIT]
cells = collections.Counter()
wrong = []
started = time.time()
for cid, csrc, cdiff, psrc, pdiff, pid, name in rows:
    repo = NATIVE / "repos" / name
    ws = repo / "nonmatchings" / name
    ck, pk = KEY(repo, ws, name, csrc), KEY(repo, ws, name, psrc)
    if ck is None or pk is None:
        cells["key-unavailable"] += 1
        continue
    same_obj = diff_body(cdiff) == diff_body(pdiff)
    cells[("key same" if ck == pk else "key differs",
           "object same" if same_obj else "object differs")] += 1
    if ck == pk and not same_obj and len(wrong) < 10:
        wrong.append({"function": name, "child": cid, "parent": pid})
key_same = cells[("key same", "object same")] + cells[("key same", "object differs")]
noops = cells[("key same", "object same")] + cells[("key differs", "object same")]
print(json.dumps({
    "stage": STAGE, "pairs": len(rows),
    "cells": {" / ".join(k) if isinstance(k, tuple) else k: v for k, v in cells.items()},
    "precision (same key => same object)": round(cells[("key same", "object same")] / max(key_same, 1), 4),
    "recall (share of no-ops this stage catches)": round(cells[("key same", "object same")] / max(noops, 1), 4),
    "violations": wrong, "seconds": round(time.time() - started, 1)}, indent=1))

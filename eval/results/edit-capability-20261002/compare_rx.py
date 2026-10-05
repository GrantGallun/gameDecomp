"""Paired: RX (lines + diagnosed rule) vs RS (lines only) vs S (planted line + planted class), on the cases RX ran."""
import collections
import json

import run

loc = {}
for r in map(json.loads, open(run.E / "attempts_localized.jsonl")):
    loc[(r["id"], r["level"])] = r["status"]
first = {}
for r in map(json.loads, open(run.E / "attempts.jsonl")):
    first[(r["id"], r["level"])] = r["status"]
ids = sorted({i for i, lv in loc if lv == "RX"})
by = collections.defaultdict(collections.Counter)
for i in ids:
    cls = i.split(":")[0]
    b = by[cls]
    b["n"] += 1
    b["RX"] += loc[(i, "RX")] == "exact"
    b["RS"] += loc.get((i, "RS")) == "exact"
    b["S"] += first.get((i, "S")) == "exact"
    b["RX_only"] += loc[(i, "RX")] == "exact" and loc.get((i, "RS")) != "exact"
    b["RS_only"] += loc.get((i, "RS")) == "exact" and loc[(i, "RX")] != "exact"
    b["RX_nc"] += loc[(i, "RX")] == "not-compiled"
    b["RS_nc"] += loc.get((i, "RS")) == "not-compiled"
print(f"{'class':12} {'n':>3} {'RS lines':>9} {'RX +rule':>9} {'S planted':>10}   RX-only  RS-only")
tot = collections.Counter()
for cls, b in sorted(by.items()):
    tot.update(b)
    print(f"{cls:12} {b['n']:>3} {b['RS']:>9} {b['RX']:>9} {b['S']:>10}   {b['RX_only']:>7}  {b['RS_only']:>7}")
print(f"{'all':12} {tot['n']:>3} {tot['RS']:>9} {tot['RX']:>9} {tot['S']:>10}   {tot['RX_only']:>7}  {tot['RS_only']:>7}")
print(f"not compiling: RS {tot['RS_nc']}, RX {tot['RX_nc']}")

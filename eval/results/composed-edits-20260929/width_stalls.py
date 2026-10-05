"""Width-stage stalls in focus2: which extension kinds are imbalanced, and what the instruction around them does."""
import collections, json, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import residual_classes as rc, diffrepair
db = sqlite3.connect("file:/home/grant/decomp/runs/composed-edits-20260929/focus2.sqlite?mode=ro", uri=True)
addr = {n: a for a, n in db.execute("select addr, name from functions")}
rows = [json.loads(l) for l in (HERE / "focus2-B207.jsonl").read_text().splitlines()]
out = {}
kinds = collections.Counter(); ctx = collections.Counter()
for r in rows:
    atts = db.execute("select source_code, diff_summary from attempts where func_addr=? and compiled=1", (addr[r["function"]],)).fetchall()
    if not atts: continue
    src, diff = min(atts, key=lambda a: tuple(rc.counts(a[1] or "")[k] for k in rc.CLASSES))
    c = rc.counts(diff or "")
    if c["control_flow"] or not c["width"]: continue
    t, cand = diffrepair._streams(diff or "")
    a, b = rc._extension_kinds(t), rc._extension_kinds(cand)
    delta = {f"{k[0]} {k[1]}": a[k] - b[k] for k in set(a) | set(b) if a[k] != b[k]}   # + target has more
    # consumer of each unmatched extension: the next instruction that reads its destination
    def consumers(stream, want):
        res = []
        for i, x in enumerate(stream):
            if not rc.EXTENSION.match(x): continue
            parts = x.replace(",", " ").split()
            if (parts[0], parts[-1]) not in want: continue
            dst = parts[1]
            for y in stream[i + 1:i + 6]:
                if dst in y.replace(",", " ").split()[1:]:
                    res.append(y.split()[0] + (" (store)" if y.split()[0] in ("sb", "sh", "sw") else "")); break
            else:
                res.append("?")
        return res
    more_t = {k for k in set(a) | set(b) if a[k] > b[k]}; more_c = {k for k in set(a) | set(b) if b[k] > a[k]}
    ct, cc = consumers(t, more_t), consumers(cand, more_c)
    for k, v in delta.items(): kinds[("target has more" if v > 0 else "candidate has more", k)] += 1
    for x in ct: ctx[("target ext ->", x)] += 1
    for x in cc: ctx[("cand ext ->", x)] += 1
    out[r["function"]] = {"band": r["band"], "delta": delta, "target_consumers": ct, "cand_consumers": cc,
                          "compiles": r.get("compiles")}
print("width-stage functions:", len(out), collections.Counter(v["band"] for v in out.values()))
print("imbalance kinds (functions):"); [print("  ", k, v) for k, v in kinds.most_common(12)]
print("consumers:"); [print("  ", k, v) for k, v in ctx.most_common(14)]
(HERE / "width_stalls.json").write_text(json.dumps(out, indent=1))

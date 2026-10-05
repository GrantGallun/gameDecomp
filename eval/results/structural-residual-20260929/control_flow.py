"""Control-flow differences in unsolved best attempts, split into signatures, against branch_shape's gates."""
import sys, sqlite3, collections, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import diffrepair
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
size = {n: ic for n, ic in C.execute("select name,insn_count from functions")}
best = {}
for db in (C, K):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d in db.execute("select func_addr, max(score), diff_summary from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, d)
COND = {"beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz", "bc1t", "bc1f", "beql", "bnel", "beqzl", "bnezl", "bgezl", "bltzl"}
UNC = {"b", "j"}
def op(x): return x.split()[0] if x.split() else x
def count(stream):
    c = collections.Counter()
    for x in stream:
        o = op(x)
        if o in COND: c["cond"] += 1; c["cond:" + o] += 1
        elif o in UNC: c["uncond"] += 1
        elif o == "jr" and not x.endswith("ra"): c["jumptable"] += 1
        elif o == "jr": c["return"] += 1
        elif o.startswith("slt"): c["slt"] += 1
    return c
tags = collections.Counter(); band_tags = collections.defaultdict(collections.Counter); nfn = collections.Counter()
rows = {}
for n, (sc, d) in best.items():
    t, c = diffrepair._streams(d or "")
    if not t or not c: continue
    ct, cc = count(t), count(c)
    f = set()
    if ct["jumptable"] != cc["jumptable"]: f.add("switch lowered differently (jump table vs compare chain)")
    if ct["cond"] != cc["cond"]: f.add("conditional-branch count differs")
    if ct["uncond"] > cc["uncond"]: f.add("target has more b/j (select_else / return-tail gates)")
    if ct["uncond"] < cc["uncond"]: f.add("candidate has more b/j")
    if ct["return"] != cc["return"]: f.add("return count differs")
    if ct["slt"] > cc["slt"]: f.add("target has more slt (split_merge gate)")
    if ct["cond"] == cc["cond"] and any(ct["cond:" + k] != cc["cond:" + k] for k in COND):
        f.add("same count, inverted/different condition kinds")
    if not f: continue
    b = "small" if (size.get(n) or 0) < 50 else "medium" if size.get(n) < 150 else "large+"
    nfn[b] += 1
    for k in f: tags[k] += 1; band_tags[b][k] += 1
    gated = {"target has more b/j (select_else / return-tail gates)", "target has more slt (split_merge gate)"}
    rows[n] = {"band": b, "tags": sorted(f), "any_gate_matches": bool(f & gated), "only_gated": f <= gated}
print("functions with a control-flow count/kind difference:", sum(nfn.values()), dict(nfn))
print(f"{'signature':62s} {'fns':>4s} {'small':>6s} {'med':>5s} {'large+':>7s}")
for k, v in tags.most_common():
    print(f"{k:62s} {v:4d} {band_tags['small'][k]:6d} {band_tags['medium'][k]:5d} {band_tags['large+'][k]:7d}")
print("some branch_shape gate matches:", sum(r["any_gate_matches"] for r in rows.values()),
      " all differences inside gated signatures:", sum(r["only_gated"] for r in rows.values()))
Path(__file__).with_name("control_flow.json").write_text(json.dumps(rows, indent=1))

"""Where do sign/zero-extension differences sit? call argument, return value, incoming parameter, or local."""
import sys, sqlite3, collections, json, re, difflib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import signals, diffrepair
C = sqlite3.connect("file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro", uri=True)
K = sqlite3.connect("file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro", uri=True)
exact = set()
for db in (C, K):
    exact |= {r[0] for r in db.execute("select f.name from functions f join attempts a on a.func_addr=f.addr where a.exact=1")}
best = {}
for name, db in (("campaign", C), ("kb", K)):
    amap = {a: n for a, n in db.execute("select addr,name from functions")}
    for a, sc, d, i, s in db.execute("select func_addr, max(score), diff_summary, id, strategy from attempts where compiled=1 and exact=0 group by func_addr"):
        n = amap.get(a)
        if n and n not in exact and (n not in best or sc > best[n][0]): best[n] = (sc, d, name, i, s)
EXT = re.compile(r"^(sll|sra|srl)\s+(\w+),(\w+),(0x10|16|0x18|24)$|^andi\s+(\w+),(\w+),(0xff|0xffff)$")
def op(x):
    m = signals.OPCODE.match(x); return m.group(1) if m else x
def site(stream, i):
    ins = stream[i]; m = EXT.match(ins)
    dst = m.group(2) or m.group(5); src = m.group(3) or m.group(6)
    # follow an sll->sra pair to its final destination
    if op(ins) == "sll" and i + 1 < len(stream):
        m2 = EXT.match(stream[i + 1])
        if m2 and op(stream[i + 1]) in ("sra", "srl"): dst = m2.group(2)
    ahead = stream[i + 1:i + 6]
    if dst in ("a0", "a1", "a2", "a3") and any(op(x) in ("jal", "jalr") for x in ahead): return "call argument"
    behind = stream[max(0, i - 4):i]
    if src == "v0" and any(op(x) in ("jal", "jalr") for x in behind): return "return of callee"
    if dst == "v0" and any(op(x) == "jr" for x in ahead): return "own return value"
    if src in ("a0", "a1", "a2", "a3") and i < 12: return "incoming parameter"
    if any(re.match(r"s[bh]\s+" + re.escape(dst) + ",", x) for x in ahead): return "narrow store"
    return "local/expression"
per_fn = collections.defaultdict(collections.Counter); units = collections.Counter(); by_source = collections.Counter()
for n, (sc, d, ledger, aid, strat) in best.items():
    t, c = diffrepair._streams(d or "")
    if not t or not c: continue
    sm = difflib.SequenceMatcher(None, t, c, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal": continue
        for k in range(i1, i2):
            if EXT.match(t[k]): per_fn[n]["missing@" + site(t, k)] += 1
        for k in range(j1, j2):
            if EXT.match(c[k]): per_fn[n]["extra@" + site(c, k)] += 1
fns = collections.Counter(); only = collections.Counter()
for n, cnt in per_fn.items():
    kinds = {k.split("@")[1] for k in cnt}
    for k in kinds: fns[k] += 1
    for k, v in cnt.items(): units[k] += v
    if len(kinds) == 1: only[next(iter(kinds))] += 1
    by_source["binary-types" if "binary" in (best[n][4] or "") else "other"] += 1
print("functions with any extension difference:", len(per_fn))
print("functions by site kind:", fns.most_common())
print("functions with a single site kind:", only.most_common())
print("units:", units.most_common())
print("best attempt strategy family:", by_source)
Path(__file__).with_name("width_sites.json").write_text(json.dumps({n: dict(c) for n, c in per_fn.items()}, indent=1))

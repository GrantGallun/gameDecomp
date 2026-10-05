"""Structural bucket, second cut: remove moved instructions, tag each function by concrete patterns."""
import sys, sqlite3, collections, re, difflib
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import signals, diffrepair
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
def op(x):
    m = signals.OPCODE.match(x); return m.group(1) if m else x
CF = set(signals.BRANCH) | {"j", "jal", "jr", "jalr", "b", "beqzl", "bnezl", "bnel", "beql", "bgezl", "bltzl"}
def band(ic): return "small" if ic < 50 else "medium" if ic < 150 else "large+"
tags = collections.Counter(); tags_band = collections.defaultdict(collections.Counter)
only = collections.Counter(); n_struct = 0; nb = collections.Counter()
for n, (sc, d) in best.items():
    t, c = diffrepair._streams(d or "")
    if not t or not c: continue
    s = signals.analyse(d, sc, False, True)
    if not s.structural: continue
    n_struct += 1; b = band(size.get(n) or 0); nb[b] += 1
    sm = difflib.SequenceMatcher(None, [op(x) for x in t], [op(x) for x in c], autojunk=False)
    miss, extra, swaps, brt = [], [], [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            brt += sum(1 for x, y in zip(t[i1:i2], c[j1:j2]) if x != y and op(x) in CF and op(x) != "jal")
        elif tag == "replace":
            k = min(i2 - i1, j2 - j1)
            swaps += list(zip(t[i1:i1 + k], c[j1:j1 + k])); miss += t[i1 + k:i2]; extra += c[j1 + k:j2]
        elif tag == "delete": miss += t[i1:i2]
        else: extra += c[j1:j2]
    # swaps count as a miss+extra pair for pattern detection
    miss_all = miss + [a for a, _ in swaps]; extra_all = extra + [b_ for _, b_ in swaps]
    mc, ec = collections.Counter(miss_all), collections.Counter(extra_all)
    moved = sum((mc & ec).values())
    mc, ec = mc - ec, ec - mc                      # verbatim moved instructions removed
    M, E = list(mc.elements()), list(ec.elements())
    f = set()
    ft = [x for x in t if re.match(r"addiu\s+sp,sp,-", x)]; fc = [x for x in c if re.match(r"addiu\s+sp,sp,-", x)]
    if ft != fc: f.add("frame size differs")
    stack = [x for x in M + E if re.search(r"\(sp\)", x) and op(x) in ("sw", "lw", "sh", "lh", "lwc1", "swc1", "sdc1", "ldc1")]
    if stack: f.add("stack spill/save traffic")
    if any(op(x) in CF for x in M + E): f.add("branch/jump count or kind")
    if brt and not any(op(x) in CF for x in M + E): f.add("branch targets shifted only")
    if any(re.match(r"(sll|sra)\s+\w+,\w+,(0x10|16|0x18|24)$", x) for x in M + E) or any(op(x) == "andi" and re.search(r",(0xff|0xffff)$", x) for x in M + E):
        f.add("sign/zero-extension (type width)")
    if any(op(x) in ("mult", "multu", "mflo") for x in M + E) and any(op(x) in ("sll", "subu", "addu") for x in M + E):
        f.add("index arithmetic form (mult vs shift/add)")
    if any(op(x) == "lui" for x in M + E): f.add("address materialisation (lui reuse/hoist)")
    if any(op(x) == "move" or (op(x) in ("addu", "or") and ",zero" in x.replace(" ", "")) for x in M + E): f.add("register copies (move)")
    if any(op(x) == "nop" for x in M + E): f.add("delay-slot nop")
    if moved: f.add("moved instructions (scheduling)")
    if any(op(x) in ("lw", "sw", "lh", "lhu", "lb", "lbu", "sh", "sb") and not re.search(r"\(sp\)", x) for x in M + E): f.add("non-stack memory op added/removed")
    if len(t) != len(c): f.add("instruction count differs")
    for k in f: tags[k] += 1; tags_band[b][k] += 1
    if len(f) == 1: only[next(iter(f))] += 1
print("functions whose best attempt has a 'structural' fault:", n_struct, dict(nb))
print(f"\n{'pattern':46s} {'fns':>5s} {'small':>6s} {'medium':>7s} {'large+':>7s}")
for k, v in tags.most_common():
    print(f"{k:46s} {v:5d} {tags_band['small'][k]:6d} {tags_band['medium'][k]:7d} {tags_band['large+'][k]:7d}")
print("\nfunctions with exactly one pattern:", dict(only))

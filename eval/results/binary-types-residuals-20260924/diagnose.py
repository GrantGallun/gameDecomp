"""What separates the remaining SMALL binary-typed drafts from a match? No compiles.

1. The harness-exact functions that were never confirmed: their last official attempt's verdict.
2. The small compiled-not-exact drafts (search flat or improved): residual classes of their best known draft, via the
   project's own classifier (eval.mechanism_roadmap.classes over the harness diff), grouped into type-ish kinds
   (width/signedness/mask/extension, offsets/immediates) versus allocation/scheduling kinds (register, order)."""
import collections, json, re, sqlite3, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/draft-reference-mining-20260924")
import mine
from eval import mechanism_roadmap

E = Path.home() / "decomp/experiments/binary-types-capability-20260924"
WS = Path.home() / "decomp/experiments/draft-reference-mining-20260924/repo/nonmatchings"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
exact = {n for (n,) in conn.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr where a.exact=1")}

state, rows = {}, {}
for d in ("rows", "v2/rows", "v3/rows"):
    for p in (E / d).glob("*.json"):
        x = json.loads(p.read_text()); state[p.stem] = x["status"]; rows[p.stem] = x
search = {p.stem: json.loads(p.read_text()) for p in (E / "search").glob("*.json")}

# 1. unconfirmed harness exacts
unconf = [n for n, s in state.items() if s == "exact" and n not in exact]
why = collections.Counter(); ex = {}
for n in unconf:
    r = conn.execute("select a.compiled, a.score, a.exact, a.sampling from attempts a join functions f on f.addr=a.func_addr "
                     "where f.name=? and a.run_id like 'binary-types%' order by a.id desc limit 1", (n,)).fetchone()
    if not r:
        k = "no official attempt"
    else:
        fe = (json.loads(r[3]) or {}).get("frontend") if r[3] else None
        k = ("official not compiled" if not r[0] else f"official score {'100' if r[1] == 100 else '<100'}; gate {'fail' if fe and fe.get('passed') is False else 'ok/none'}")
    why[k] += 1; ex.setdefault(k, n)
print("1. unconfirmed harness-exact:", len(unconf), dict(why), ex)

# 2. residual classes of small compiled-not-exact drafts
def diff_of(t, c):
    with tempfile.TemporaryDirectory() as d:
        a, b = Path(d) / "t", Path(d) / "c"
        a.write_text("\n".join(t) + "\n"); b.write_text("\n".join(c) + "\n")
        return subprocess.run(["diff", "-u", str(a), str(b)], capture_output=True, text=True).stdout
TYPEISH = re.compile(r"(andi|sll|sra|srl|lb|lbu|lh|lhu|sb|sh|lw|sw|lwc1|swc1|mtc1|cvt|div|mult|slt|sltu|sltiu|slti)")
kinds, per_fn, n_fn = collections.Counter(), collections.Counter(), 0
sole_register = 0
for n, s in state.items():
    if n in exact or s != "compiled":
        continue
    t_path = WS / n / "target_object_dump_normalized.s"
    c_path = WS / n / "bintypes_object_dump_normalized.s"
    if not t_path.exists() or not c_path.exists():
        continue
    t, c = mine.mask(t_path.read_text()), mine.mask(c_path.read_text())
    if len(t) >= 50 or t == c:
        continue
    n_fn += 1
    cls = {k for k, *_ in mechanism_roadmap.classes(diff_of(t, c), None)}
    for k in cls:
        kinds[k] += 1
    fam = set()
    for k in cls:
        if k == "field:register":
            fam.add("register")
        elif k.startswith(("field:offset", "field:immediate")):
            fam.add("offset/immediate")
        elif k.startswith(("extra:", "missing:", "opcode:")) and TYPEISH.search(k):
            fam.add("width/sign/op")
        elif k.startswith(("extra:", "missing:", "opcode:")):
            fam.add("other-instruction")
        else:
            fam.add(k.split(":")[0] + ":" + k.split(":")[1] if ":" in k else k)
    per_fn[" + ".join(sorted(fam))] += 1
    sole_register += fam == {"register"}
print("2. small compiled-not-exact with current bintypes dumps:", n_fn, "register-only:", sole_register)
for k, v in per_fn.most_common(10):
    print("   ", v, k)
print("   top classes:", kinds.most_common(14))

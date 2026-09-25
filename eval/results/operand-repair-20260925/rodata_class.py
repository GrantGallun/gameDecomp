"""How much of the campaign frontier is the rodata-literal class? Read-only.

A function is in the class when every differing aligned step of its best attempt differs ONLY in relocation symbol
names where the target names a data symbol (D_/jtbl_/named) and the candidate uses a section-relative reference
(.rodata/.data/.bss), or ALSO in registers (then it is class-plus-registers). Counted for the 130 pool and the whole
compiled-not-exact frontier."""
import collections, json, re, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import alignment

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
frontier = json.loads((HERE.parent / "frontier-20260924/frontier.json").read_text())["rows"]
fronts = json.loads((HERE.parent / "frontier-20260924/fronts.json").read_text())["rows"]
pool = {r["function"] for r in fronts if r["structural"] == 0 and r["size"] in ("small", "medium")}
SEC = re.compile(r"%(hi|lo)\(\.(rodata|data|bss)(\+0x[0-9a-f]+)?\)")
NAMED = re.compile(r"%(hi|lo)\([A-Za-z_]\w*(\+0x[0-9a-f]+)?\)")
REG = re.compile(r"\b(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|fp|ra|gp|sp)\b")


def kind(diff):
    kinds = collections.Counter()
    for st in alignment.align_diff(diff).steps:
        t, c = st.target, st.candidate
        if st.ambiguous or (t and c and t.text == c.text):
            continue
        if t is None or c is None:
            kinds["structural"] += 1
            continue
        tt, ct = t.text, c.text
        if NAMED.search(tt) and SEC.search(ct) and NAMED.sub("%R", tt) == SEC.sub("%R", ct):
            kinds["rodata-symbol"] += 1
        elif REG.sub("R", tt) == REG.sub("R", ct):
            kinds["register"] += 1
        elif NAMED.search(tt) and SEC.search(ct) and REG.sub("R", NAMED.sub("%R", tt)) == REG.sub("R", SEC.sub("%R", ct)):
            kinds["rodata-symbol"] += 1
            kinds["register"] += 1
        else:
            kinds["other"] += 1
    return kinds


out = collections.Counter()
members = collections.defaultdict(list)
for r in frontier:
    diff = camp.execute("select diff_summary from attempts where id=?", (r["attempt"],)).fetchone()[0] or ""
    if not diff.lstrip().startswith(("---", "@@")):
        continue
    k = kind(diff)
    if not k["rodata-symbol"]:
        continue
    cls = ("rodata-only" if set(k) == {"rodata-symbol"} else
           "rodata+register" if set(k) <= {"rodata-symbol", "register"} else "rodata+other")
    scope = "pool" if r["function"] in pool else "frontier"
    out[(scope, cls)] += 1
    members[cls].append(r["function"])
print({f"{a}|{b}": v for (a, b), v in sorted(out.items())})
(HERE / "rodata_class.json").write_text(json.dumps({k: sorted(v) for k, v in members.items()}, indent=1))

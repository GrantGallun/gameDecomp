"""Is temp removal exhausted on the allocator pool? For the campaign functions whose best attempt has zero structural
steps (frontier-20260924/fronts.py), count locals in the best source that are assigned once and read once (inlinable
temporaries), and whether the campaign already tried a temp_remove edit on that function. Read-only."""
import collections, json, re, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import n64_corpus
import mine_alloc as ma

H = Path.home() / "decomp"
fronts = json.loads((Path(__file__).resolve().parent.parent / "frontier-20260924/fronts.json").read_text())
frontier = json.loads((Path(__file__).resolve().parent.parent / "frontier-20260924/frontier.json").read_text())
attempt = {r["function"]: r["attempt"] for r in frontier["rows"]}
pool = [r["function"] for r in fronts["rows"] if r["structural"] == 0 and r["size"] in ("small", "medium")]
db = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
addr = {n: a for a, n in db.execute("select addr, name from functions")}
out = collections.Counter()
examples = []
for name in pool:
    src = db.execute("select source_code from attempts where id=?", (attempt[name],)).fetchone()[0] or ""
    rec = next((r for r in n64_corpus.extract_functions(src) if r["name"] == name), None)
    if rec is None:
        out["no definition"] += 1
        continue
    body = n64_corpus._mask_noncode(str(rec["definition"]))
    body = body[body.find("{"):]
    decls = re.findall(r"^\s*(?:register\s+)?" + ma.TYPES + r"[\s\*]+(\w+)\s*;", body, re.M)
    single = []
    for v in decls:
        assigns = len(re.findall(rf"\b{v}\s*=[^=]", body))
        reads = len(re.findall(rf"\b{v}\b", body)) - assigns - 1
        if assigns == 1 and reads == 1:
            single.append(v)
    tried = 0
    for (p, c) in db.execute("select e.parent_attempt_id, e.child_attempt_id from attempt_edges e join attempts a "
                             "on a.id=e.child_attempt_id where a.func_addr=?", (addr[name],)):
        ps = db.execute("select source_code from attempts where id=?", (p,)).fetchone()[0]
        cs = db.execute("select source_code from attempts where id=?", (c,)).fetchone()[0]
        if ma.edit_type(ma.body_lines(ps, name), ma.body_lines(cs, name)) == "temp_remove":
            tried += 1
            break
    out["with single-use temps" if single else "no single-use temps"] += 1
    out["campaign tried temp_remove" if tried else "temp_remove never tried"] += 1
    if single and not tried and len(examples) < 8:
        examples.append((name, single[:4]))
print(len(pool), dict(out), examples)

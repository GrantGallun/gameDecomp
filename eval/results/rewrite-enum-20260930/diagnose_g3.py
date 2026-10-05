"""Why Engine G rarely fired on the G3 frame: per function, G entries that score, that apply, and the rank of the best
G proposal among all mined proposals (the lane takes 8)."""
import json, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import rewrite_library, rule_miner, term_rewrite as tr

HERE = Path(__file__).resolve().parent
db = sqlite3.connect("/home/grant/decomp/runs/rewrite-enum-20260930/g.sqlite")
table = rule_miner.Table.load()
names = [json.loads(l)["function"] for l in (HERE / "g3.jsonl").read_text().splitlines()]
rows = []
for fn in names:
    r = db.execute("select a.source_code, a.diff_summary from attempts a join functions f on f.addr=a.func_addr "
                   "where f.name=? and a.strategy like 'g:baseline%' order by a.id limit 1", (fn,)).fetchone()
    if not r:
        continue
    src, diff = r
    res = rule_miner.features(diff or "")
    scoring = [(table.score(k, res), k) for k, e in table.rules.items() if k.startswith("G:") and not e.get("pruned")]
    scoring = [x for x in scoring if x[0] > 0]
    b, e = rewrite_library._body(src, fn)
    trees = tr.index(tr.body_trees(src, b, e))
    guard = tr.integer_guard(src)
    applicable = []
    for s, k in scoring:
        lhs, rhs = rule_miner.inverse(k)[2:].split(" => ", 1)
        if next(tr.matches(trees, tr.pattern(lhs), src, guard), None):
            applicable.append((s, k))
    # which forward directions (validated, any profile) have a match at all
    anymatch = sum(1 for k, e in table.rules.items() if k.startswith("G:") and not e.get("pruned")
                   and next(tr.matches(trees, tr.pattern(rule_miner.inverse(k)[2:].split(" => ")[0]), src, guard), None))
    props = rule_miner.proposals(src, fn, diff, table=table, limit=40)
    rank = next((i for i, p in enumerate(props) if p[1].startswith("G:")), None)
    rows.append({"function": fn, "g_scoring": len(scoring), "g_applicable": len(applicable), "g_forward_matching": anymatch,
                 "g_rank": rank, "proposals": len(props)})
print(json.dumps(rows[:5], indent=0))
n = len(rows)
print(f"functions {n}; with a scoring G entry {sum(r['g_scoring'] > 0 for r in rows)}; "
      f"with a scoring AND applicable one {sum(r['g_applicable'] > 0 for r in rows)}; "
      f"with any usable G direction matching (ignoring score) {sum(r['g_forward_matching'] > 0 for r in rows)}; "
      f"G in top 8 {sum(r['g_rank'] is not None and r['g_rank'] < 8 for r in rows)}")
(HERE / "diagnose_g3.json").write_text(json.dumps(rows, indent=1))

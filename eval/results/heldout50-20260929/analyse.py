"""Pre-registered analysis: paired sign test on best gradient (treatment vs control), plus secondary measures."""
import collections, json, math, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import signals

RUNS = Path("/home/grant/decomp/runs/heldout50-20260929")


def best_by_arm(arm, names):
    db = sqlite3.connect(f"file:{RUNS / (arm + '.sqlite')}?mode=ro", uri=True)
    addr = {n: a for a, n in db.execute("select addr, name from functions")}
    out = {}
    for n in names:
        rows = db.execute("select compiled, exact, score, diff_summary, strategy from attempts where func_addr=?", (addr[n],)).fetchall()
        best = None
        for c, e, sc, d, st in rows:
            g = (1, 10**9, 10**9) if not c else (0, 0, 0) if e else (0, *signals.distances(d or ""))
            if best is None or (g, -(sc or 0)) < (best[0], -best[1]):
                best = (g, sc or 0, st)
        out[n] = best
    return out


def sign_test_p(wins, losses):
    """One-sided exact binomial P(X >= wins | n = wins + losses, p = 0.5)."""
    n = wins + losses
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n if n else 1.0


t_rows = {json.loads(l)["function"]: json.loads(l) for l in (HERE / "treatment.jsonl").read_text().splitlines()}
c_rows = {json.loads(l)["function"]: json.loads(l) for l in (HERE / "control.jsonl").read_text().splitlines()}
names = sorted(set(t_rows) & set(c_rows))
tb, cb = best_by_arm("treatment", names), best_by_arm("control", names)
wins, losses, ties = [], [], []
for n in names:
    (tg, ts, tst), (cg, cs, cst) = tb[n], cb[n]
    (wins if tg < cg else losses if tg > cg else ties).append(n)
p = sign_test_p(len(wins), len(losses))
print(f"paired functions: {len(names)}  treatment wins {len(wins)}  losses {len(losses)}  ties {len(ties)}  one-sided p = {p:.4f}")
print("DECISION:", "positive and significant -> mature, move on" if len(wins) > len(losses) and p < 0.05 else "not significant -> continue")
print("wins:", wins); print("losses:", losses)
ex_t = [n for n in names if t_rows[n].get("exact")]; ex_c = [n for n in names if c_rows[n].get("exact")]
print("exact treatment:", ex_t, " control:", ex_c)
delta = [(tb[n][1] - cb[n][1]) for n in names]
print(f"mean best-score difference (treatment - control): {sum(delta) / len(delta):+.3f}")
print("compiles treatment:", sum(t_rows[n].get("compiles") or 0 for n in names), " control:", sum(c_rows[n].get("compiles") or 0 for n in names))
db = sqlite3.connect(f"file:{RUNS / 'treatment.sqlite'}?mode=ro", uri=True)
fam = collections.Counter(st.split(":")[2] for (st,) in db.execute("select strategy from attempts where strategy like 'heldout-treatment:shape:%'"))
print("shape edits compiled in treatment, by family:", dict(fam))
print("shape family on the winning best state:", collections.Counter(tb[n][2].split(":")[2] if ":shape:" in tb[n][2] else "non-shape" for n in wins))

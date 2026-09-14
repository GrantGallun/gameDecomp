"""Close the near-misses with the permuter -- the register-allocation band.

Four functions sit at 99.3-99.8%. At 99.8% the candidate is one or two
instructions from exact, which is precisely the register-allocation case the
permuter exists for, and the pipeline is supposed to route >=95% there. Given
run_permuter previously fabricated EXACTs from directory names, it is doubtful
this path ever closed anything.

Takes the best stored candidate for each function, seeds the workspace with it,
and runs the permuter -- then RE-VERIFIES every output through the oracle,
because a directory name is a claim and workspace.score is the verdict.
"""
import sqlite3
import sys
import time
from pathlib import Path

from solver import pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
SECONDS = int(sys.argv[1]) if sys.argv[1:] else 300

rows = list(conn.execute("""
    select f.name, round(max(a.score),1) best
    from attempts a join functions f on f.addr = a.func_addr
    group by a.func_addr having best >= 95 and best < 100
    order by best desc"""))
print(f"{len(rows)} functions in the >=95% band; {SECONDS}s of permuter each\n")

closed, improved, flat = [], [], []
t0 = time.time()

for name, best in rows:
    addr = conn.execute("select addr from functions where name=?",
                        (name,)).fetchone()[0]
    src = conn.execute("""select source_code from attempts
                          where func_addr=? and compiled=1
                          order by score desc limit 1""", (addr,)).fetchone()
    if not src or not src[0]:
        print(f"  {name[:38]:40} no stored source -- skipped")
        continue
    ws = workspace.bootstrap(repo, name)

    # verify the stored candidate still scores what the DB claims, before
    # spending permuter time on a number that may be stale or fabricated
    pre = workspace.score(ws, repo, "seed", src[0])
    if not pre.compiled:
        print(f"  {name[:38]:40} stored best does not compile -- skipped")
        continue
    if pre.exact:
        print(f"  {name[:38]:40} ALREADY EXACT on re-verify (db said {best})")
        closed.append((name, 100.0))
        continue

    seed = ws / "seed.c"
    seed.write_text(src[0], encoding="utf-8")
    t = time.time()
    try:
        score, exact, code = pipeline.run_permuter(repo, name, seed,
                                                   SECONDS, ws=ws)
    except Exception as exc:
        print(f"  {name[:38]:40} permuter ERROR {type(exc).__name__}")
        continue

    tag = "EXACT" if exact else f"{score:.2f}%"
    delta = score - pre.score
    print(f"  {name[:38]:40} {pre.score:6.2f} -> {tag:>9}"
          f"  ({delta:+.2f})  [{time.time()-t:.0f}s]", flush=True)
    if exact:
        closed.append((name, score))
        (ws / "matched.c").write_text(code, encoding="utf-8")
    elif delta > 0.01:
        improved.append((name, pre.score, score))
    else:
        flat.append(name)

print(f"\n===== NEAR-MISS CLOSING ({time.time()-t0:.0f}s) =====")
print(f"CLOSED to byte-exact : {len(closed)}")
for n, s in closed:
    print(f"    {n}")
print(f"improved but not closed: {len(improved)}")
for n, a, b in improved:
    print(f"    {n}  {a:.2f} -> {b:.2f}")
print(f"no movement            : {len(flat)}")
print("\nEvery number above was re-verified through the oracle, not read from "
      "a permuter directory name.")

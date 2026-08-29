"""T2: mechanically repair struct layouts across the >=90% band.

For each function, take its best candidate on disk, rewrite every struct it
declares to the layout the evidence tier observed, and re-score. Deterministic,
LLM-free, no GPU.

Tries each struct name against each observed base, because the candidate's
naming gives no reliable mapping from struct to base register. Cheap enough to
brute force, and the oracle is the arbiter.
"""
import sqlite3
import sys
from pathlib import Path

from solver import structgen, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")

rows = conn.execute("""
    select f.name, round(max(a.score),1) best
      from attempts a join functions f on f.addr = a.func_addr
     group by a.func_addr having best >= 90 and best < 100
     order by best desc""").fetchall()
targets = [r[0] for r in rows]
if sys.argv[1:]:
    targets = targets[:int(sys.argv[1])]
print(f"{len(targets)} functions in the 90-100 band\n")

closed, improved, flat, skipped = [], [], [], []

for func in targets:
    try:
        ws = workspace.bootstrap(repo, func)
    except Exception:
        skipped.append((func, "no workspace"))
        continue

    best, code = 0.0, ""
    for cand in sorted((repo / "nonmatchings").glob(f"{func}-*/output-*/source.c")):
        try:
            t = cand.read_text(errors="replace")
            a = workspace.score(ws, repo, "b", t, conn=conn, func=func,
                                strategy="structfix-baseline")
        except Exception:
            continue
        if a.exact:
            best, code = a.score, t
            break
        if a.score > best:
            best, code = a.score, t
    if not code:
        skipped.append((func, "no candidate on disk"))
        continue
    if best >= 100:
        closed.append((func, 100.0))
        Path("matched_recovered").mkdir(exist_ok=True)
        Path(f"matched_recovered/{func}.c").write_text(code, encoding="utf-8")
        print(f"  {func[:40]:42} already exact on disk")
        continue

    lay = structgen.layout(conn, func)
    names = structgen.struct_names(code)
    if not lay or not names:
        skipped.append((func, f"layout={len(lay)} structs={len(names)}"))
        continue

    top, top_att = best, None
    best_code = code
    for base, fields in lay.items():
        for nm in names:
            new, changed = structgen.rewrite(code, nm, fields)
            if not changed:
                continue
            try:
                att = workspace.score(ws, repo, "sfix", new, conn=conn,
                                      func=func, strategy="structfix")
            except Exception:
                continue
            if att.exact:
                top, best_code, top_att = 100.0, new, att
                break
            if att.score > top:
                top, best_code, top_att = att.score, new, att
        if top_att is not None and top_att.exact:
            break

    delta = top - best
    tag = "EXACT" if (top_att and top_att.exact) else f"{top:.2f}%"
    print(f"  {func[:40]:42} {best:6.2f} -> {tag:>9}  ({delta:+.2f})",
          flush=True)
    if top_att is not None and top_att.exact:
        closed.append((func, 100.0))
        Path("matched_recovered").mkdir(exist_ok=True)
        Path(f"matched_recovered/{func}.c").write_text(best_code,
                                                       encoding="utf-8")
    elif delta > 0.01:
        improved.append((func, best, top))
    else:
        flat.append(func)

print(f"\n===== T2 STRUCT REPAIR =====")
print(f"CLOSED to byte-exact : {len(closed)}")
for f, _ in closed:
    print(f"    {f}")
print(f"improved             : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.2f} -> {b:.2f}")
print(f"no movement          : {len(flat)}")
print(f"skipped              : {len(skipped)}")
for f, why in skipped[:6]:
    print(f"    {f}: {why}")

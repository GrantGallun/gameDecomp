"""T2b: mechanically repair struct layouts across the >=90% band.

Re-run of T2 after two fixes that made the first run uninterpretable:
  - structgen preserved no field names, so every rewrite renamed field28 to
    field_28 and broke the function body. The resulting COMPILE FAILURE was
    reported as "no improvement", hiding a fix one padding byte from exact.
  - struct_names() matched "} break;" and returned ['break'], so functions with
    no structs looked like "rewrite applied and did not help".

This harness therefore counts four distinct outcomes and never conflates them:
  applied      the rewrite changed the source
  broke        it applied and the result stopped compiling
  improved     it applied, compiled, and scored higher
  closed       byte-exact

Mechanism proven twice: updateTimeTrialRecordDeltaPopupSlideIn 99.61 -> 100.00
and SlideOut 99.999 -> 100.00, both by padding alone.
"""
import sqlite3
import sys
from pathlib import Path

from solver import structgen, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
out_dir = Path("matched_recovered")
out_dir.mkdir(exist_ok=True)

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
n_applied = n_broke = 0
layouts_seen = []

for func in targets:
    try:
        ws = workspace.bootstrap(repo, func)
    except Exception:
        skipped.append((func, "no workspace"))
        continue

    base, code = 0.0, ""
    already = False
    for cand in sorted((repo / "nonmatchings").glob(f"{func}-*/output-*/source.c")):
        try:
            t = cand.read_text(errors="replace")
            a = workspace.score(ws, repo, "t2b_base", t, conn=conn, func=func,
                                strategy="t2b-baseline")
        except Exception:
            continue
        if a.exact:
            base, code, already = 100.0, t, True
            break
        if a.score > base:
            base, code = a.score, t
    if not code:
        skipped.append((func, "no candidate on disk"))
        continue
    if already:
        closed.append(func)
        (out_dir / f"{func}.c").write_text(code, encoding="utf-8")
        print(f"  {func[:40]:42} already exact")
        continue

    lay = structgen.layout(conn, func)
    names = structgen.struct_names(code)
    layouts_seen.append(len(lay))
    if not lay or not names:
        skipped.append((func, f"layout={len(lay)} structs={len(names)}"))
        continue

    top, top_code, top_exact, broke_here = base, code, False, 0
    for fields in lay.values():
        for nm in names:
            # repad, not rewrite: regenerating the struct from ONE
            # function's evidence deletes every field that function does not
            # touch, and the body stops compiling. Only padding is resized.
            m = structgen._struct_pattern(nm).search(code)
            if not m:
                continue
            observed = {o: w for o, w, _ in fields}
            body2, changed = structgen.repad(m.group(0), observed)
            if not changed:
                continue
            new = code[:m.start()] + body2 + code[m.end():]
            n_applied += 1
            try:
                att = workspace.score(ws, repo, "t2b_fix", new, conn=conn,
                                      func=func, strategy="t2b-structfix")
            except Exception:
                continue
            if not att.compiled:
                broke_here += 1
                n_broke += 1
                continue
            if att.exact:
                top, top_code, top_exact = 100.0, new, True
                break
            if att.score > top:
                top, top_code = att.score, new
        if top_exact:
            break

    tag = "EXACT" if top_exact else f"{top:.3f}%"
    note = f"  [{broke_here} broke]" if broke_here else ""
    print(f"  {func[:40]:42} {base:7.3f} -> {tag:>9}  ({top-base:+.3f}){note}",
          flush=True)
    if top_exact:
        closed.append(func)
        (out_dir / f"{func}.c").write_text(top_code, encoding="utf-8")
    elif top - base > 0.0005:
        improved.append((func, base, top))
    else:
        flat.append(func)

assert any(layouts_seen), ("every layout was EMPTY -- the evidence query is "
                           "broken, not the data. Not a null result.")
print(f"\n===== T2b STRUCT REPAIR =====")
print(f"rewrites applied     : {n_applied}   (of which broke the build: {n_broke})")
print(f"CLOSED to byte-exact : {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved             : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.3f} -> {b:.3f}")
print(f"no movement          : {len(flat)}")
print(f"skipped              : {len(skipped)}")
for f, why in skipped[:8]:
    print(f"    {f}: {why}")

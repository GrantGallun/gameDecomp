"""Does repad recover what prefill's terseness threw away?

Prefill raises the mean (16.69 -> 25.42) but drops struct padding, which cost
the one exact match: isRacePlayerRespawnSurfaceValid wrote
"s32 field1C; s32 field24; s16 field502;" with no padding, so those fields sat
at 0, 4, 8 instead of 0x1C, 0x24, 0x502.

repad() resizes padding so named fields land on their observed offsets, and the
prefilled candidates name fields BY offset, which is exactly the signal it
needs. Prefill for reach, repad for correctness.

Operates on tonight's prefill candidates specifically, not on the best-ever
candidate, or it would skip functions that already have an exact match on file.
"""
import datetime as dt
import sqlite3
from pathlib import Path

from solver import structgen, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
CUT = dt.datetime(2026, 8, 29, 0, 0).timestamp()
out_dir = Path("matched_recovered")
out_dir.mkdir(exist_ok=True)

best: dict[str, tuple[float, str]] = {}
for name, src, sc in conn.execute(
        "select f.name, a.source_code, a.score from attempts a"
        " join functions f on f.addr=a.func_addr"
        " where a.created_at >= ? and a.compiled = 1 and a.score < 100"
        " and a.source_code is not null", (CUT,)):
    if src and sc > best.get(name, (-1.0, ""))[0]:
        best[name] = (sc, src)

print(f"{len(best)} functions with a prefilled compiling candidate\n")
closed, improved, flat = [], [], []
applied = broke = nostruct = nolayout = nothing = 0

for name, (score, src) in sorted(best.items(), key=lambda kv: -kv[1][0]):
    lay = structgen.layout(conn, name)
    names = structgen.struct_names(src)
    if not names:
        nostruct += 1
        continue
    if not lay:
        nolayout += 1
        continue
    try:
        ws = workspace.bootstrap(repo, name)
    except Exception:
        continue

    top, top_code, top_exact, changed_any = score, src, False, False
    for fields in lay.values():
        observed = {o: w for o, w, _ in fields}
        for nm in names:
            m = structgen._struct_pattern(nm).search(src)
            if not m:
                continue
            body, changed = structgen.repad(m.group(0), observed)
            if not changed:
                continue
            changed_any = True
            applied += 1
            cand = src[:m.start()] + body + src[m.end():]
            try:
                att = workspace.score(ws, repo, "rpf", cand, conn=conn,
                                      func=name, strategy="prefill+repad")
            except Exception:
                continue
            if not att.compiled:
                broke += 1
                continue
            if att.exact:
                top, top_code, top_exact = 100.0, cand, True
                break
            if att.score > top:
                top, top_code = att.score, cand
        if top_exact:
            break

    if not changed_any:
        nothing += 1
        continue
    tag = "EXACT" if top_exact else f"{top:.3f}%"
    print(f"  {name[:40]:42} {score:7.3f} -> {tag:>9}  ({top-score:+.3f})",
          flush=True)
    if top_exact:
        closed.append(name)
        (out_dir / f"{name}.c").write_text(top_code, encoding="utf-8")
    elif top - score > 0.0005:
        improved.append((name, score, top))
    else:
        flat.append(name)

print(f"\n===== PREFILL + REPAD =====")
print(f"repads applied        : {applied}  (broke the build: {broke})")
print(f"CLOSED to byte-exact  : {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved              : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.3f} -> {b:.3f}")
print(f"no movement           : {len(flat)}")
print(f"skipped: no struct {nostruct}, no layout {nolayout}, "
      f"padding already right {nothing}")

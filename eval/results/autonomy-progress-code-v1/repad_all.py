"""Apply repad to the best STORED candidate for every function.

Earlier passes globbed permuter output directories, which exist for only a
handful of functions. The attempts table stores source_code for every logged
attempt: 40 of 75 functions have a struct in their best candidate, and several
sit at 99.99x -- displayed as "100.00" by two-decimal formatting, which is
exactly how SlideOut hid a one-padding-byte fix.

CPU only. No model, no GPU.
"""
import sqlite3
import sys
from pathlib import Path

from solver import structgen, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
out_dir = Path("matched_recovered")
out_dir.mkdir(exist_ok=True)

best: dict[str, tuple[float, str]] = {}
for name, src, sc in conn.execute(
        "select f.name, a.source_code, a.score from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.compiled = 1 and a.score < 100 and a.source_code is not null"
        " and a.source_code != ''"):
    if src and sc > best.get(name, (-1.0, ""))[0]:
        best[name] = (sc, src)

targets = sorted(best.items(), key=lambda kv: -kv[1][0])
if sys.argv[1:]:
    targets = targets[:int(sys.argv[1])]
print(f"{len(targets)} functions with a stored compiled candidate\n")

closed, improved, flat = [], [], []
applied = broke = no_struct = no_layout = nothing_to_fix = 0

for name, (score, src) in targets:
    lay = structgen.layout(conn, name)
    if not lay:
        no_layout += 1
        continue
    names = structgen.struct_names(src)
    if not names:
        no_struct += 1
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
                att = workspace.score(ws, repo, "repad", cand, conn=conn,
                                      func=name, strategy="repad-all")
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
        nothing_to_fix += 1
        continue
    tag = "EXACT" if top_exact else f"{top:.3f}%"
    print(f"  {name[:42]:44} {score:7.3f} -> {tag:>9}  ({top - score:+.3f})",
          flush=True)
    if top_exact:
        closed.append(name)
        (out_dir / f"{name}.c").write_text(top_code, encoding="utf-8")
    elif top - score > 0.0005:
        improved.append((name, score, top))
    else:
        flat.append(name)

print(f"\n===== REPAD OVER ALL STORED CANDIDATES =====")
print(f"rewrites applied   : {applied}  (broke the build: {broke})")
print(f"CLOSED byte-exact  : {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved           : {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.3f} -> {b:.3f}")
print(f"no movement        : {len(flat)}")
print(f"skipped -- no struct in candidate : {no_struct}")
print(f"skipped -- no param evidence      : {no_layout}")
print(f"skipped -- padding already correct: {nothing_to_fix}")

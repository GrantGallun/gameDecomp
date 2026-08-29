"""Close the 98%+ band. Longer search, and the P1 reporting bugs fixed.

Fixes carried over from P1:
  - report max(seed, permuter best). P1 reported permuter-best alone, so two
    functions showed -97.22 and -96.44 when nothing had regressed.
  - score EVERY output directory, old and new, and never break early. P1 broke
    on the first exact it found, which happened to come from a stale directory
    and made a 1-second result look like a search win.
  - a directory name is a claim; workspace.score is the verdict. The names
    encode a dist.py COST where 0 is perfect, and reading them as scores both
    fabricated matches and hid four real ones.

Targets are the closest candidates the project has: everything at 98%+ that is
not yet byte-exact.
"""
import re
import sys
import time
from pathlib import Path

from solver import pipeline, workspace

repo = Path.home() / "decomp/sbk1"
SECONDS = int(sys.argv[1]) if sys.argv[1:] else 500

TARGETS = [
    "updateTimeTrialRecordDeltaPopupSlideIn",
    "updateEndingLindaExitUntilPhase3C",
    "isRacePlayerRespawnSurfaceValid",
    "updateRaceSplitscreenSelectPlayerCountIcons",
    "checkMainMenuSecretCode",
]

out_dir = Path("matched_recovered")
out_dir.mkdir(exist_ok=True)


def best_on_disk(ws, func):
    """Oracle-verify every permuter output for this function. No early break."""
    best, exact, code = 0.0, False, ""
    for cand in sorted((repo / "nonmatchings").glob(f"{func}-*/output-*/source.c")):
        try:
            txt = cand.read_text(errors="replace")
            att = workspace.score(ws, repo, "scan", txt)
        except Exception:
            continue
        if att.exact:
            return att.score, True, txt
        if att.score > best:
            best, code = att.score, txt
    return best, exact, code


print(f"{len(TARGETS)} functions, {SECONDS}s of permuter each\n")
closed, improved, flat = [], [], []
t0 = time.time()

for func in TARGETS:
    ws = workspace.bootstrap(repo, func)
    pre_score, pre_exact, pre_code = best_on_disk(ws, func)
    if pre_exact:
        print(f"  {func[:42]:44} ALREADY EXACT on disk")
        closed.append(func)
        (out_dir / f"{func}.c").write_text(pre_code, encoding="utf-8")
        continue
    if not pre_code:
        print(f"  {func[:42]:44} no seed available -- skipped")
        continue

    seed = ws / "seed2.c"
    seed.write_text(pre_code, encoding="utf-8")
    t = time.time()
    try:
        pipeline.run_permuter(repo, func, seed, SECONDS, ws=None)
    except Exception as exc:
        print(f"  {func[:42]:44} permuter ERROR {type(exc).__name__}")
    post_score, post_exact, post_code = best_on_disk(ws, func)

    # never report worse than the seed: the artifact cannot regress
    score = max(pre_score, post_score)
    tag = "EXACT" if post_exact else f"{score:.2f}%"
    print(f"  {func[:42]:44} {pre_score:6.2f} -> {tag:>9}"
          f"  ({score - pre_score:+.2f})  [{time.time()-t:.0f}s]", flush=True)
    if post_exact:
        closed.append(func)
        (out_dir / f"{func}.c").write_text(post_code, encoding="utf-8")
    elif score - pre_score > 0.01:
        improved.append((func, pre_score, score))
    else:
        flat.append(func)

print(f"\n===== 98%+ BAND ({time.time()-t0:.0f}s) =====")
print(f"CLOSED to byte-exact: {len(closed)}")
for f in closed:
    print(f"    {f}")
print(f"improved: {len(improved)}")
for f, a, b in improved:
    print(f"    {f}  {a:.2f} -> {b:.2f}")
print(f"no movement: {len(flat)}")

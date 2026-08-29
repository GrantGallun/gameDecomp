"""Constrained glasses: enumerate call-argument orderings, let the oracle pick.

The remaining error on isRacePlayerRespawnSurfaceValid is an argument-to-
register mapping:

    target     a0 = field_502   a1 = field_1c   a2 = field_24
    candidate  a0 = field_1c    a1 = field_24   a2 = field_502

The model would not fix this when shown the diff -- but it does not have to.
Three arguments have six orderings, and the compiler decides. No reverse
engineering of an assembly hunk to a C location is required.

This is level 3 of the three kinds of guidance:
  1 textual   target asm + raw diff             tested, limited
  2 semantic  aligned blocks, ABI mappings      mostly untested
  3 constrained  a finite set of mechanical     this
                 alternatives, oracle-verified
"""
import itertools
import re
import sys
from pathlib import Path

from solver import workspace

repo = Path.home() / "decomp/sbk1"
FUNC = sys.argv[1] if sys.argv[1:] else "isRacePlayerRespawnSurfaceValid"
SRC = Path("matched_recovered") / f"{FUNC}.c"

ws = workspace.bootstrap(repo, FUNC)

# start from the best candidate on disk for this function
cand = None
for p in (SRC, ws / "hrpad.c", ws / "dr1.c", ws / "hr0.c"):
    if p.exists():
        cand = p.read_text(errors="replace")
        break
if cand is None:
    print("no candidate source found")
    raise SystemExit(1)

base = workspace.score(ws, repo, "ap_base", cand)
print(f"baseline: {base.score:.3f}  exact={base.exact}\n")

CALL = re.compile(r"(\w+)\s*\(\s*([^();]+?)\s*\)")
TYPEWORD = re.compile(r"\b(s8|u8|s16|u16|s32|u32|f32|f64|int|char|void|"
                      r"long|short|struct|unsigned|extern)\b")
# Skip DECLARATIONS. An earlier version matched the extern prototype and
# permuted its PARAMETER NAMES, which changes nothing -- six variants all
# scored identically, which looked like "argument order is not the problem"
# when in fact the call site was never touched.
calls = [(m.group(1), m.group(2)) for m in CALL.finditer(cand)
         if m.group(2).count(",") >= 1
         and not TYPEWORD.search(m.group(2))
         and "return" not in m.group(0)]
if not calls:
    print("no multi-argument call found to permute")
    raise SystemExit(0)

name, args = calls[0]
parts = [a.strip() for a in args.split(",")]
print(f"permuting call: {name}({', '.join(parts)})  -> {len(parts)}! orderings\n")

best, best_code, found = base.score, cand, False
for i, order in enumerate(itertools.permutations(parts)):
    if list(order) == parts:
        continue                       # the baseline ordering
    new_call = f"{name}({', '.join(order)})"
    variant = cand.replace(f"{name}({args})", new_call, 1)
    if variant == cand:
        variant = re.sub(re.escape(name) + r"\s*\([^();]+?\)",
                         new_call, cand, count=1)
    if variant == cand:
        continue
    att = workspace.score(ws, repo, f"ap{i}", variant)
    tag = "EXACT" if att.exact else (f"{att.score:.3f}" if att.compiled
                                     else "no compile")
    print(f"  {', '.join(order):52} {tag}")
    if att.exact:
        best, best_code, found = 100.0, variant, True
        break
    if att.compiled and att.score > best:
        best, best_code = att.score, variant

print(f"\nbest {base.score:.3f} -> {best:.3f}  exact={found}")
if found:
    Path("matched_recovered").mkdir(exist_ok=True)
    (Path("matched_recovered") / f"{FUNC}.c").write_text(best_code,
                                                         encoding="utf-8")
    print("BYTE-EXACT. Saved.")

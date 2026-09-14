"""Constrained glasses, multi-axis: enumerate combinations, oracle ranks them.

Single-axis attempts on isRacePlayerRespawnSurfaceValid all fell short --
struct padding (+0.000), callee signature (+0.058), argument order (+0.096).
Three wrong predictions is the finding: a function at 99.6% has SEVERAL small
interacting errors, so fixing one in isolation moves the score by a fraction of
a point.

So enumerate the product rather than one axis at a time. No model is involved;
the oracle ranks every variant and cannot be fooled.

Axes, each mechanically derived from the source:
  argument order   permutations of one call site's arguments
  signedness       s16 <-> s32 on locals feeding that call
  temporaries      pass struct fields directly instead of via locals

Hill-climbs: keep any variant that scores higher, then enumerate again from
there, so combinations compound instead of being tried only in isolation.
"""
import itertools
import re
import sys
from pathlib import Path

from solver import workspace

repo = Path.home() / "decomp/sbk1"
FUNC = sys.argv[1] if sys.argv[1:] else "isRacePlayerRespawnSurfaceValid"
ROUNDS = int(sys.argv[2]) if sys.argv[2:] else 3

ws = workspace.bootstrap(repo, FUNC)
src = None
for p in [Path("matched_recovered") / f"{FUNC}.c", ws / "ap_base.c",
          ws / "hrpad.c"]:
    if p.exists():
        src = p.read_text(errors="replace")
        break
if src is None:
    print("no candidate found")
    raise SystemExit(1)

TYPEWORD = re.compile(r"\b(s8|u8|s16|u16|s32|u32|f32|f64|int|char|void|long|"
                      r"short|struct|unsigned|extern)\b")
CALL = re.compile(r"(\w+)\s*\(\s*([^();]+?)\s*\)")


def variants(code: str) -> list[tuple[str, str]]:
    """Every one-step neighbour of `code`, as (label, source)."""
    out = []

    # --- axis 1: argument order at each real call site --------------------
    for m in CALL.finditer(code):
        name, args = m.group(1), m.group(2)
        if args.count(",") < 1 or TYPEWORD.search(args):
            continue                      # a declaration, not a call
        parts = [a.strip() for a in args.split(",")]
        if len(parts) > 4:
            continue                      # 24+ orderings, skip
        for order in itertools.permutations(parts):
            if list(order) == parts:
                continue
            out.append((f"argorder {name}({','.join(order)})",
                        code[:m.start()] + f"{name}({', '.join(order)})"
                        + code[m.end():]))

    # --- axis 2: signedness of locals -------------------------------------
    for decl in re.finditer(r"\b(s16|s32|u16|u32)\s+(\w+)\s*=", code):
        ty, var = decl.group(1), decl.group(2)
        for alt in ("s16", "s32", "u16", "u32"):
            if alt == ty:
                continue
            out.append((f"type {var}: {ty}->{alt}",
                        code[:decl.start()] + f"{alt} {var} ="
                        + code[decl.end():]))

    # --- axis 3: drop a temporary, pass the field directly ----------------
    for decl in re.finditer(r"\b(?:s8|u8|s16|u16|s32|u32)\s+(\w+)\s*=\s*"
                            r"([^;]+?);", code):
        var, expr = decl.group(1), decl.group(2).strip()
        if "->" not in expr and "." not in expr:
            continue
        body = code[:decl.start()] + code[decl.end():]
        inlined = re.sub(rf"\b{re.escape(var)}\b", f"({expr})", body)
        if inlined != body:
            out.append((f"inline temp {var}", inlined))
    return out


cur = src
att = workspace.score(ws, repo, "ef_base", cur)
best = att.score
print(f"baseline {best:.3f}  exact={att.exact}\n")

for rnd in range(1, ROUNDS + 1):
    vs = variants(cur)
    print(f"round {rnd}: {len(vs)} variants")
    improved = None
    for i, (label, code) in enumerate(vs):
        if code == cur:
            continue
        a = workspace.score(ws, repo, f"ef{rnd}_{i}", code)
        if a.exact:
            print(f"   {label:46} EXACT")
            Path("matched_recovered").mkdir(exist_ok=True)
            (Path("matched_recovered") / f"{FUNC}.c").write_text(
                code, encoding="utf-8")
            print(f"\nBYTE-EXACT after {rnd} round(s). Saved.")
            raise SystemExit(0)
        if a.compiled and a.score > best + 0.0005:
            print(f"   {label:46} {a.score:.3f}  (+{a.score-best:.3f})")
            best, improved = a.score, code
    if improved is None:
        print(f"   no improvement; stopping at {best:.3f}")
        break
    cur = improved

print(f"\nfinal {best:.3f}, not exact")

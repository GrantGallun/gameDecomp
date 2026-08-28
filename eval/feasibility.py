"""Screen eval functions for harness-infeasibility.

Some functions cannot be matched under the harness's own rules, and scoring
them as model failures corrupts the number. The concrete case: `build.sh`
rejects any candidate containing a `do` token --

    ERROR: The C file contains a do-while loop.

-- while SBK1's own `initMenuAssetHandles` is written with `do { ... } while`.
The correct answer is forbidden, so no model can ever score it. It sat in the
eval set as a 0 and dragged the reported match rate down.

This screens the eval set against the known-good source BEFORE running, so
infeasible functions are excluded and *reported* rather than silently counted
as failures. Ground truth is used to CHECK the eval set, never to build a
prompt -- see the contamination rules in CLAUDE.md.

Run:
    python3 -m eval.feasibility --repo ~/decomp/sbk1 --functions a,b,c
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# Constructs build.sh refuses to compile. Keep in sync with its guards.
FORBIDDEN = {
    "do-while": re.compile(r"\bdo\b\s*\{"),
    "global-asm": re.compile(r"\b(?:GLOBAL_ASM|INCLUDE_ASM)\b"),
    "inline-asm": re.compile(r"\b__asm__|\basm\s*\("),
}

COMMENTS_AND_LITERALS = re.compile(
    r'//[^\r\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)


def source_of(repo: Path, func: str) -> str | None:
    """The function's body from the finished decomp, for checking only."""
    proc = subprocess.run(
        ["bash", "-lc",
         f"grep -rn --include=*.c -A 60 '^[a-zA-Z_].*[ *]{func}(' src/ | head -80"],
        cwd=repo, capture_output=True, text=True)
    if not proc.stdout.strip():
        return None

    lines = [l.split(":", 2)[-1] for l in proc.stdout.splitlines()]
    text, depth, started = [], 0, False
    for line in lines:
        text.append(line)
        depth += line.count("{") - line.count("}")
        if "{" in line:
            started = True
        if started and depth <= 0:
            break
    return "\n".join(text)


def check(repo: Path, func: str) -> tuple[bool, str]:
    src = source_of(repo, func)
    if src is None:
        return True, "source not found (cannot screen; treated as feasible)"

    stripped = COMMENTS_AND_LITERALS.sub(" ", src)
    for name, pattern in FORBIDDEN.items():
        if pattern.search(stripped):
            return False, f"ground truth uses {name}, which build.sh rejects"
    return True, ""


def screen(repo: Path, funcs: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    feasible, infeasible = [], []
    for f in funcs:
        ok, reason = check(repo, f)
        (feasible if ok else infeasible).append(f if ok else (f, reason))
    return feasible, infeasible


FUNC_DEF_RE = re.compile(
    r"^[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;]*?\)\s*\{", re.MULTILINE)


def coverage(repo: Path) -> dict:
    """How much of the game the harness can even express, as its own number.

    Excluding do-while functions from the eval set was right for MEASURING THE
    MODEL and wrong for DECOMPILING THE GAME -- two different goals that got
    conflated. Quietly dropping them shrinks the denominator and flatters the
    match rate; a tool meant to decompile the whole game has to report the part
    it structurally cannot attempt.

    One pass over the sources rather than a git grep per function, which would
    take ten minutes to answer the same question.
    """
    total = blocked = 0
    by_reason: dict[str, int] = {}
    examples: dict[str, list] = {}

    for path in sorted((repo / "src").rglob("*.c")):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        stripped = COMMENTS_AND_LITERALS.sub(" ", text)

        # Walk function bodies by brace depth so a construct is attributed to
        # the function that contains it.
        for m in FUNC_DEF_RE.finditer(stripped):
            name = m.group(1)
            depth, end = 0, len(stripped)
            for i in range(m.end() - 1, len(stripped)):
                if stripped[i] == "{":
                    depth += 1
                elif stripped[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            body = stripped[m.end():end]
            total += 1
            for reason, pattern in FORBIDDEN.items():
                if pattern.search(body):
                    blocked += 1
                    by_reason[reason] = by_reason.get(reason, 0) + 1
                    examples.setdefault(reason, []).append(name)
                    break

    return {"total": total, "blocked": blocked, "by_reason": by_reason,
            "examples": {k: v[:4] for k, v in examples.items()}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--functions")
    ap.add_argument("--coverage", action="store_true",
                    help="report what fraction of the game the harness can express")
    args = ap.parse_args()

    if args.coverage:
        c = coverage(args.repo.expanduser())
        pct = 100.0 * c["blocked"] / c["total"] if c["total"] else 0.0
        print("HARNESS COVERAGE")
        print(f"  functions in src/        : {c['total']}")
        print(f"  the harness cannot accept: {c['blocked']}  ({pct:.1f}%)")
        for reason, n in sorted(c["by_reason"].items(), key=lambda kv: -kv[1]):
            print(f"    {reason:12} {n:5}   e.g. {', '.join(c['examples'][reason][:3])}")
        print(f"\n  MAXIMUM ACHIEVABLE: {100 - pct:.1f}% of the game.")
        print("  A match rate quoted over the feasible subset is not a match "
              "rate over the game.")
        return

    if not args.functions:
        ap.error("--functions is required unless --coverage is given")

    funcs = [f.strip() for f in args.functions.split(",") if f.strip()]
    feasible, infeasible = screen(args.repo.expanduser(), funcs)

    print(f"feasible   : {len(feasible)}/{len(funcs)}")
    for f in feasible:
        print(f"  ok        {f}")
    if infeasible:
        print(f"\nINFEASIBLE : {len(infeasible)} -- exclude from scoring")
        for f, reason in infeasible:
            print(f"  excluded  {f}: {reason}")
        print("\nCounting these as model failures understates the match rate.")
    print("\n" + ",".join(feasible))


if __name__ == "__main__":
    main()

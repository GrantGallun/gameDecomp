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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--functions", required=True)
    args = ap.parse_args()

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

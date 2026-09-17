"""Remove the `do`-token ban from the matching helper, in every copy actually used.

The ban is a self-imposed policy in the agent scoring helper, not a compiler limit: the reference
project's own ROM-verified source uses `do { ... } while (...)` at 387 sites across 53 files, and its
`make` build compiles them. The helper refuses the token and tells the agent to rewrite the loop, which
silently changes codegen -- measured on drawRaceSplitscreenSelectOption2Frame, where the mandated
`for (;;) { ...; if (!(...)) break; }` lowering alone takes the function from matching to 99.395.

Removed on explicit operator instruction ("get rid of the dumb restrictions"). Three kinds of copy:

    the checked-in helper            external/snowboardkids-decomp/tools/claude-decomp-env/build.sh
    the live helper                  ~/decomp/sbk1/tools/claude-decomp-env/build.sh
    per-workspace adapted scripts    nonmatchings/*/.compiler-*.sh   (2,015 of them)

Per-workspace `build.sh` is a symlink to the live helper, so it needs no separate edit -- but the
adapted `.compiler-*.sh` copies are separate files that ARE invoked at score time, and
`solver.uopt_diagnosis._recipe_command` picks one by sort order, so a stale copy could otherwise keep
refusing `do` while the helper had stopped. They are rewritten in place, and the next
`solver.compiler_recipe.prepare` regenerates them under a new key because `helper_sha256` changes.

    python3 eval/remove_do_ban.py [--check]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BAN = re.compile(
    r"^# Agents: This restriction is intentional; do not remove, disable, or bypass it\.\n"
    r"if python3 - \"\$INPUT\" <<'PY'\n.*?\nPY\nthen\n"
    r"\s*echo \"ERROR: The C file contains a do-while loop\.\"\n"
    r"\s*echo \"Rewrite the loop using while or for instead\.\"\n"
    r"\s*exit 1\n"
    r"fi\n",
    re.DOTALL | re.MULTILINE)

NOTE = ("# The `do`-token refusal that stood here was removed on operator instruction 2026-09-17.\n"
        "# It was a policy, not a compiler limit: the reference project's own ROM-verified source\n"
        "# uses `do { ... } while (...)` and its build compiles it. Refusing the token forced a\n"
        "# `for (;;) { ...; if (!(...)) break; }` lowering that is NOT codegen-neutral -- measured on\n"
        "# drawRaceSplitscreenSelectOption2Frame, the lowering alone turns a matching function into\n"
        "# 99.395. Restore a refusal only with evidence that the lowering is equivalent.\n")


def strip(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "do-while loop" not in text:
        return "clean"
    new, count = BAN.subn(NOTE, text)
    if count == 0:
        return "UNMATCHED"
    path.write_text(new, encoding="utf-8", errors="surrogateescape")
    return f"stripped x{count}"


def detect(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "do-while loop" not in text:
        return "clean"
    return "banned" if BAN.search(text) else "UNMATCHED"


def targets(repo: Path) -> list[Path]:
    checked_in = Path(__file__).resolve().parent / "external/snowboardkids-decomp/tools/claude-decomp-env/build.sh"
    out = [p for p in (checked_in, repo / "tools/claude-decomp-env/build.sh") if p.is_file()]
    out += sorted((repo / "nonmatchings").glob("*/.compiler-*.sh"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--check", action="store_true", help="report only; change nothing")
    args = ap.parse_args(argv)
    counts: dict[str, int] = {}
    for path in targets(args.repo):
        result = detect(path) if args.check else strip(path)
        counts[result] = counts.get(result, 0) + 1
        if result.startswith(("UNMATCHED", "stripped")) or (args.check and result == "banned"):
            print(f"  {result:<14} {path}")
    print(f"examined {sum(counts.values())} files: {counts}")
    return 1 if "UNMATCHED" in counts else 0


if __name__ == "__main__":
    sys.exit(main())

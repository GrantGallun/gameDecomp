"""Map each function to the commit that matched it, for honest replay evaluation.

Review finding 8, and the best idea in that review. Every measurement so far
gave the solver sibling source drawn from a 100%-COMPLETE decomp -- a template
library that would not exist mid-project. The caveat was written into
solver/siblings.py and then every run enabled siblings anyway, so every number
since iteration 3 is inflated by an unknown amount.

The fix is not to remove siblings; a human decompiler genuinely has them. It is
to give the solver the siblings that existed AT THE TIME. The reference repo's
4,015 commits record exactly that, because its commit subjects name the
function:

    Match drawMenuSpriteClipped
    Improve drawMenuSpriteClipped match to 90.704%

So for any function we can find the commit that first matched it, take its
PARENT, and evaluate against the project as it stood immediately before a human
solved it. Available headers, struct layouts and matched siblings then reflect
what was genuinely known, not what was known three months later.

This module builds and verifies the mapping. Checking out that state is
`worktree_at()`.

Run:
    python3 -m eval.history --repo ~/decomp/sbk1 --verify 20
"""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# "Match fooBar" / "Improve fooBar match to 90.704%" / "Match fooBar (notes)"
MATCH_RE = re.compile(r"^Match\s+([A-Za-z_]\w*)\b", re.IGNORECASE)
IMPROVE_RE = re.compile(r"^Improve\s+([A-Za-z_]\w*)\s+match\b", re.IGNORECASE)


@dataclass
class MatchPoint:
    function: str
    commit: str          # the commit that matched it
    parent: str          # project state immediately BEFORE it was matched
    subject: str
    order: int           # 0 = matched first; higher = later in the project


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, timeout=timeout).stdout


def build_map(repo: Path) -> dict[str, MatchPoint]:
    """function -> the commit that FIRST matched it.

    git log is newest-first, so the last "Match X" seen while walking is the
    earliest one chronologically. `Improve X match` commits are a fallback:
    some functions were partially matched before being finished, and their
    first appearance still marks when work on them began.
    """
    log = _git(repo, "log", "--format=%H%x01%P%x01%s")
    seen: dict[str, MatchPoint] = {}
    fallback: dict[str, MatchPoint] = {}

    lines = log.splitlines()
    total = len(lines)
    for idx, line in enumerate(lines):
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        sha, parents, subject = parts
        first_parent = parents.split()[0] if parents.strip() else ""
        if not first_parent:
            continue

        m = MATCH_RE.match(subject)
        if m:
            seen[m.group(1)] = MatchPoint(m.group(1), sha, first_parent,
                                          subject, total - idx)
            continue
        m = IMPROVE_RE.match(subject)
        if m:
            fallback[m.group(1)] = MatchPoint(m.group(1), sha, first_parent,
                                              subject, total - idx)

    for name, mp in fallback.items():
        seen.setdefault(name, mp)
    return seen


def verify(repo: Path, mapping: dict[str, MatchPoint], sample: int) -> dict:
    """Sanity-check the mapping against the tree itself.

    A mapping is only trustworthy if the function was genuinely absent from C
    before its match commit and present after. Checked directly with `git show`
    rather than assumed from the subject line, because a commit subject is a
    human's description and this is a machine's inference from it.
    """
    import random
    rng = random.Random(7)
    names = rng.sample(sorted(mapping), min(sample, len(mapping)))

    ok = asm_before = no_asm_after = 0
    problems = []
    for name in names:
        mp = mapping[name]

        # The real state transition is the assembly include disappearing.
        # Grepping for `name(` is useless here -- it matches prototypes,
        # extern declarations and call sites, all of which exist long before a
        # function is matched. An earlier version of this check reported
        # "5/20 absent before" and that number meant nothing.
        # The stub is `#pragma GLOBAL_ASM("asm/nonmatchings/<path>/<name>.s")`.
        # An earlier pattern required `<name>"` and so matched nothing, because
        # the name is followed by `.s`. It reported 1/25 and looked like a
        # mapping failure rather than a broken regex.
        pat = f"(GLOBAL_ASM|INCLUDE_ASM).*\\b{name}\\b"
        had_asm = bool(_git(repo, "grep", "-lE", pat, mp.parent, "--", "src/").strip())
        still_asm = bool(_git(repo, "grep", "-lE", pat, mp.commit, "--", "src/").strip())

        asm_before += had_asm
        no_asm_after += (not still_asm)
        # A clean match: assembly include present before, gone after.
        if had_asm and not still_asm:
            ok += 1
        else:
            why = ("no asm include before (may predate the stub convention)"
                   if not had_asm else "asm include still present after")
            problems.append((name, why))

    return {"sampled": len(names), "asm_before": asm_before,
            "no_asm_after": no_asm_after, "ok": ok, "problems": problems[:6]}


def worktree_at(repo: Path, commit: str, dest: Path) -> Path:
    """Materialise the project as it stood at `commit`.

    A worktree rather than a checkout so the primary tree is never disturbed --
    it holds the build, the ROM and the venv, and losing it costs an hour.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(dest), commit],
                   cwd=repo, capture_output=True, text=True, timeout=600)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--verify", type=int, default=0)
    args = ap.parse_args()

    repo = args.repo.expanduser()
    mapping = build_map(repo)
    print(f"functions with a match commit: {len(mapping)}")

    early = sorted(mapping.values(), key=lambda m: m.order)[:3]
    late = sorted(mapping.values(), key=lambda m: -m.order)[:3]
    print("\nearliest matched:")
    for m in early:
        print(f"  #{m.order:<5} {m.function[:38]:40} {m.commit[:8]}")
    print("latest matched:")
    for m in late:
        print(f"  #{m.order:<5} {m.function[:38]:40} {m.commit[:8]}")

    if args.verify:
        print(f"\nverifying {args.verify} against the tree...")
        r = verify(repo, mapping, args.verify)
        print(f"  asm include present BEFORE : {r['asm_before']}/{r['sampled']}")
        print(f"  asm include gone AFTER     : {r['no_asm_after']}/{r['sampled']}")
        print(f"  clean transition           : {r['ok']}/{r['sampled']}")
        if r["problems"]:
            print("  unverified:")
            for name, subj in r["problems"]:
                print(f"    {name[:34]:36} {subj}")


if __name__ == "__main__":
    main()

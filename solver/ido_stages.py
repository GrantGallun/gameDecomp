"""IDO's own front end as the canonicalizer: a line-normalized ucode key per candidate.

WHY
---
On the branch-point pilot, 58% of the model's first-round alternatives compiled to the parent's
object (analysis/noop_kinds.out): casts, index-vs-pointer spellings and loop forms were 80-88%
no-ops. A stage probe (analysis/ido_stage_probe.py) showed where they die: casts, pointer
arithmetic and blank lines produce IDENTICAL ucode -- IDO's front end has already flattened them --
while loop spellings and commuted operands survive the front end and are flattened by the optimizer.

So rather than approximating IDO's equivalences with mined rules, ask IDO: two candidates with the
same front-end output are the same program to every later stage.

HOW
---
ucode embeds line numbers, so each candidate is preprocessed with the project's exact recipe
(`cc -E`), stripped of line markers, joined onto ONE line and compiled with `-j` (stop after the
front end). Identical layout means line information cannot differ; equal bytes then mean the
front end produced the same program. Returns None -- never a guess -- when the source cannot be
normalized (pragmas, which must stay on their own line) or any step fails.

CAVEAT, to be MEASURED before relying on it: the reference team records that IDO is occasionally
line-break sensitive in scheduling, and this normalization erases line layout. "Same key => same
object" is checked on logged candidates (analysis/front_end_key_probe.py) before any use skips a
compile.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT = 60
_LINE_MARKER = re.compile(r"^\s*#(?:\s*\d+|\s*line\b)")


def _recipe_command(repo: Path, ws: Path, function: str) -> list[str]:
    from solver import compiler_experiment, compiler_recipe
    identity = json.loads((ws / ".compiler-target.json").read_text())
    if identity.get("function") != function or not identity.get("target"):
        raise ValueError("workspace compiler identity does not match function")
    recipe = compiler_recipe.resolve(repo, identity["target"])
    return compiler_experiment._direct_command(recipe["command"], repo, ws)


def one_line(preprocessed: str) -> str | None:
    """Preprocessed C whose every line reports line 1: each non-empty line is preceded by
    `#line 1`, so layout cannot change the line information in the ucode. None if a pragma is present.

    (The first version joined the whole preprocessed file onto ONE line. On real candidates that
    line is ~28,000 characters and IDO's front end silently emitted a near-empty unit -- exit 0 --
    so every candidate got the same key. The toy probe's files were too short to show it.)"""
    kept = []
    for line in preprocessed.splitlines():
        if _LINE_MARKER.match(line):
            continue
        if line.lstrip().startswith("#"):
            return None                      # a pragma or directive that must keep its own line
        if line.strip():
            kept.append('#line 1 "u.c"\n' + line.strip())
    return "\n".join(kept) + "\n"


def front_end_key(repo: Path, ws: Path, function: str, source: str) -> str | None:
    """sha256 of the line-normalized ucode IDO's front end emits for ``source``, or None."""
    return _stage_key(repo, ws, function, source, "front-end")


def optimizer_key(repo: Path, ws: Path, function: str, source: str) -> str | None:
    """sha256 of the line-normalized pre-as1 assembly (after uopt and ugen), or None. Catches the
    no-ops the optimizer flattens (loop spellings, commuted operands) that the front end does not."""
    return _stage_key(repo, ws, function, source, "optimizer")


def _stage_key(repo: Path, ws: Path, function: str, source: str, stage: str) -> str | None:
    from solver import compiler_experiment
    try:
        direct = _recipe_command(repo, ws, function)
        compiled = compiler_experiment._compile_source(repo, source)
        textconv, charmap = repo / "tools/textconv.py", repo / "tools/charmap.txt"
        with tempfile.TemporaryDirectory(prefix="ido-fe-",
                                         dir="/tmp" if os.name == "posix" else None) as temp:
            d = Path(temp)
            (d / "raw.c").write_text(compiled)
            conv = subprocess.run([sys.executable, str(textconv), str(charmap), "raw.c", "cand.c"],
                                  cwd=d, capture_output=True, text=True, timeout=TIMEOUT)
            if conv.returncode:
                return None
            pre = subprocess.run([a if a != "-c" else "-E" for a in direct] + ["cand.c"], cwd=d,
                                 capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
            if pre.returncode:
                return None
            flat = one_line(pre.stdout)
            if flat is None:
                return None
            (d / "norm.c").write_text(flat)
            base = [a for a in direct if not a.startswith("-I")]
            if stage == "front-end":
                command, unit = [*base, "-j", "norm.c"], d / "norm.u"
            else:
                command, unit = [*[a for a in base if a != "-c"], "-S", "norm.c"], d / "norm.s"
            front = subprocess.run(command, cwd=d, capture_output=True, text=True, timeout=TIMEOUT)
            if front.returncode or not unit.is_file():
                return None
            data = unit.read_bytes()
            if stage == "optimizer":
                # Drop assembler bookkeeping that carries no code: comments, .loc/.file, .ident.
                data = b"\n".join(l for l in data.splitlines()
                                  if not re.match(rb"\s*(#|\.loc\b|\.file\b|\.ident\b)", l))
            if function.encode() not in data:
                # A unit without the function is not a key: it is how the first version failed
                # silently (every candidate hashed to the same near-empty unit).
                return None
            return hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None

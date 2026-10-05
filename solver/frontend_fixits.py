"""Mechanical fixes for project frontend (clang policy) rejections: casts at the diagnosed expression.

The campaign compiles with IDO and separately requires the candidate to pass a
strict clang frontend (`solver.frontend_check`). Many rejected candidates are only
type-check failures: `-Wincompatible-pointer-types`, `-Wint-conversion` and
pointer arithmetic on an incomplete type. On 2026-09-14, 29 compiled pending
functions were stuck there, and `fadeOutAllMusicSequences` was already
object-exact. A cast to the type clang names changes nothing IDO emits for pointer
and integer conversions of the same width, so the object oracle still decides:
a fix that changes codegen fails its compile check.

`diagnose` parses clang output printed with `-fdiagnostics-print-source-range-info`;
`apply` wraps each diagnosed range in a cast; `propose` runs the project's own
frontend command repeatedly until it passes or stops making progress.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import tempfile

HEADER = re.compile(r"^candidate\.c:(?P<line>\d+):(?P<col>\d+):(?P<ranges>(?:\{\d+:\d+-\d+:\d+\})*):? ?error: (?P<msg>.+)$", re.M)
RANGE = re.compile(r"\{(\d+):(\d+)-(\d+):(\d+)\}")
QUOTED = r"'([^']+)'(?: \(aka '[^']+'\))?"
TARGET_TYPE = [
    re.compile(rf"passing {QUOTED} to parameter of type {QUOTED}"),
    re.compile(rf"assigning to {QUOTED} from {QUOTED}"),
    re.compile(rf"initializing {QUOTED} with an expression of type {QUOTED}"),
    re.compile(rf"returning {QUOTED} from a function with result type {QUOTED}"),
]
INCOMPLETE = re.compile(r"arithmetic on a pointer to an incomplete type")


@dataclass(frozen=True)
class Fix:
    line: int
    col: int
    end_line: int
    end_col: int              # exclusive: one past the last character (measured on real clang output)
    cast: str
    message: str


def _cast_type(message: str) -> str | None:
    for index, pattern in enumerate(TARGET_TYPE):
        match = pattern.search(message)
        if match:
            # passing A to B / returning A from B: the target is the second type;
            # assigning to B from A / initializing B with A: the first.
            return match.group(2) if index in (0, 3) else match.group(1)
    if INCOMPLETE.search(message):
        return "u8 *"
    return None


def diagnose(output: str) -> list[Fix]:
    fixes = []
    for match in HEADER.finditer(output):
        cast = _cast_type(match.group("msg"))
        ranges = [tuple(map(int, r)) for r in RANGE.findall(match.group("ranges"))]
        if not cast or not ranges:
            continue
        # The expression to convert is the last range for argument/assignment
        # diagnostics (clang lists the full statement first when it adds one).
        line, col, end_line, end_col = ranges[-1]
        fixes.append(Fix(line, col, end_line, end_col, cast.strip(), match.group("msg")[:200]))
    return fixes


TOKEN_END = re.compile(r"[A-Za-z_]\w*|0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*|->|\+\+|--|\S")


def _offset(lines: list[str], line: int, col: int) -> int:
    return sum(len(text) + 1 for text in lines[:line - 1]) + col - 1


def apply(source: str, fixes: list[Fix]) -> str:
    """Wrap disjoint or nested ranges using boundaries in the original source."""
    lines = source.split("\n")
    spans = []
    for fix in fixes:
        start = _offset(lines, fix.line, fix.col)
        end = _offset(lines, fix.end_line, fix.end_col)
        if end <= start:                       # zero-width range: take the token at the caret
            token = TOKEN_END.match(source, start)
            end = token.end() if token else start + 1
        # A range that closes on an opening bracket is incomplete; skip rather than guess.
        if end <= start or source[start:end].count("(") != source[start:end].count(")"):
            continue
        spans.append((start, end, fix.cast))
    spans = sorted(set(spans))
    rejected = set()
    for i, (a, b, cast) in enumerate(spans):
        for j in range(i + 1, len(spans)):
            c, d, other = spans[j]
            if c >= b:
                break
            # Crossing ranges cannot both be complete nested expressions. Two
            # different target types for exactly one range are ambiguous too.
            if a < c < b < d or (a == c and b == d and cast != other):
                rejected.update((i, j))
    openings, closings = {}, {}
    for i, (start, end, cast) in enumerate(spans):
        if i in rejected:
            continue
        openings.setdefault(start, []).append((end, cast))
        closings[end] = closings.get(end, 0) + 1
    output, cursor = [], 0
    for position in sorted(openings.keys() | closings.keys()):
        output.append(source[cursor:position])
        output.append('))' * closings.get(position, 0))
        # An outer expression opens before an inner expression at the same
        # position. Insertions never change any later range's original bounds.
        for _end, cast in sorted(openings.get(position, []), reverse=True):
            output.append(f'(({cast}) (')
        cursor = position
    output.append(source[cursor:])
    return ''.join(output)


def run_frontend(repo: Path, source: str, command: list[str], workdir: Path) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="frontend-fixit-", dir=workdir) as directory:
        raw = Path(directory) / "raw.c"
        converted = Path(directory) / "candidate.c"
        raw.write_text(source)
        conversion = subprocess.run(["python3", "tools/textconv.py", "tools/charmap.txt", str(raw), str(converted)],
                                    cwd=repo, capture_output=True, text=True, timeout=30)
        if conversion.returncode:
            return conversion.returncode, conversion.stderr
        process = subprocess.run([*command, "-fdiagnostics-print-source-range-info", "-fno-caret-diagnostics",
                                  str(converted)], cwd=repo, capture_output=True, text=True, timeout=60)
        return process.returncode, (process.stdout + process.stderr).replace(str(converted), "candidate.c")


def propose(repo: Path, source: str, command: list[str], workdir: Path, rounds: int = 4,
            *, allow_partial: bool = False) -> tuple[str | None, list[dict]]:
    """Return a frontend pass, or an explicitly requested measured partial hypothesis.

    Partial sources still require IDO scoring/logging by the caller. Existing
    normalization consumers retain their passing-frontend-only contract.
    """
    log = []
    current = source
    best_partial, best_errors = None, None
    baseline_complete = False
    for index in range(rounds + 1):
        code, output = run_frontend(repo, current, command, workdir)
        errors = len(re.findall(r': (?:fatal )?error:', output))
        # A failed command or truncated report is not a zero-error candidate.
        complete = (errors > 0 and len(list(HEADER.finditer(output))) == errors
                    and 'too many errors emitted' not in output)
        if index == 0:
            baseline_complete = complete
        if baseline_complete and complete and (best_errors is None or errors < best_errors):
            best_errors = errors
            best_partial = current if current != source else None
        if code == 0:
            return (current if current != source else None), log
        fixes = diagnose(output) if index < rounds else []
        updated = apply(current, fixes)
        log.append({"round": index, "errors": errors, "complete": complete,
                    "fixes": [f.message for f in fixes]})
        if updated == current:
            return (best_partial if allow_partial else None), log
        current = updated
    return None, log

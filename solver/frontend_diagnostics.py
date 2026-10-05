"""Clang's view of a candidate: every blocker at once, where cfe truncates at the first.

WHY THIS EXISTS. `solver/frontend_check.py` runs the project's own `CC_CHECK=clang -fsyntax-only` policy
and writes the diagnostics to `<name>.frontend.json`. Eight modules in `solver/` are written against those
diagnostics and gate on their exact text:

    undeclared_identifiers.undefined_names      `'X' undefined`
    negative_field_repair.propose               `member reference base type`
    void_field_repair.propose                   `member reference base type 'void' is not a structure`
    byte_array_decay / frontend_repair          `incompatible pointer types assigning`
    modelrepair                                 `implicit declaration of function` + `M2C_MEMCPY_ALIGNED`

On the size-bucketed frame that family was starved: the intake route never ran the frontend, so
`negative_field_repair` and `void_field_repair` declined on all 40 states for want of a gate string while
their motivating residual was sitting in the drafts. Measured by hand on six blocked states
(`eval/results/intake-20260921/_frontend_chain.py`): clang produced 4 to 19 `member reference base type`
diagnostics each, and cfe had reported ONE error for the same file.

THE TWO VIEWS ARE NOT REDUNDANT, and the difference is structural rather than a matter of strictness:

    cfe    stops at the first error and truncates its list there
    clang  reports every independent blocker in one pass

So an intake step that only reads cfe sees one blocker and cannot tell whether the draft is one fix from
compiling or thirty. This module surfaces the clang view as an OBSERVATION.

IT DOES NOT CHANGE THE CANDIDATE. It is a diagnostic projection, like `uopt-trace`: it changes what a
policy knows, never the source. Nothing here promotes, and nothing here is a claim about correctness --
the byte certificate remains the only one.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

from solver import frontend_check

# The gate strings the repair family matches on, so a caller can see WHICH mechanisms this candidate
# makes reachable without re-implementing their tests. Kept as data, next to the modules they belong to.
GATES: dict[str, str] = {
    "undeclared_identifiers": r"'[A-Za-z_]\w*' undefined|undeclared identifier",
    "negative_field_repair": r"member reference base type",
    "void_field_repair": r"member reference base type 'void' is not a structure or union",
    "byte_array_decay": r"incompatible pointer types assigning",
    "frontend_repair": r"incompatible pointer types assigning",
    "m2c_aligned_copy": r"implicit declaration of function.*\n?.*M2C_MEMCPY_ALIGNED|M2C_MEMCPY_ALIGNED",
    "scalar_header_prototypes": r"implicit declaration of function|unknown type name",
}

_ERROR = re.compile(r"^candidate\.c:(?P<line>\d+):(?P<col>\d+):\s*(?P<kind>error|warning|note):\s*"
                    r"(?P<what>.*)$")
_LOCATED_ERROR = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+):\s*"
                            r"(?P<kind>(?:fatal )?error):\s*(?P<what>.*)$")
_ANY_ERROR = re.compile(r"(?:^|: )((?:fatal )?error):")

# THE CHECKER'S OWN TRUNCATION, WHICH NOTHING USED TO READ. clang stops after `-ferror-limit` errors (20 by
# default) and says so in a `fatal error:` line -- a kind `_ERROR` does not match, so the notice was parsed
# as nothing and the short list was reported with `errors_truncated=False`. The recipe now passes
# `-ferror-limit=0` (`solver/frontend_check.recipe`), so this should never fire; it stays because a recipe
# is read from the project's Makefile and can change under us, and a silent ceiling on the error list is a
# silent ceiling on every defect-class set built from it.
_LIMIT = re.compile(r"too many errors emitted|-ferror-limit=")

# The recipe is resolved once per (repo, target): `frontend_check.recipe` shells out to `make`, which costs
# more than the compile it configures, and the answer cannot change inside one process. THE TARGET IS PART
# OF THE KEY AND IS NOT OPTIONAL -- the first version of this cached by repo alone and passed an empty
# target, so every call raised `ValueError: unsupported TU object identity` and the whole frontend looked
# unavailable on eight states out of eight. A projection is per translation unit; there is no repo-wide
# answer to cache.
_RECIPE_CACHE: dict[tuple[str, str], dict] = {}


def errors_are_complete(report: dict) -> bool:
    """A failed checker without counted errors is never a complete observation."""
    status, count = report.get('status'), report.get('error_count')
    return (status in ('passed', 'rejected') and not report.get('errors_truncated')
            and report.get('errors_complete') is not False
            and count == len(report.get('errors', []))
            and ((status == 'passed' and count == 0) or (status == 'rejected' and count > 0)))


def recipe(repo: Path, target: str) -> dict:
    key = (str(Path(repo).resolve()), target)
    if key not in _RECIPE_CACHE:
        _RECIPE_CACHE[key] = frontend_check.recipe(key[0], (Path(repo) / "Makefile").read_text(), target)
    return _RECIPE_CACHE[key]


def analyse(source: str, *, repo: Path, target: str, timeout: int = 60,
            full_diagnostics: bool = False) -> dict:
    """Run clang on `source` under the project's check policy and project its diagnostics.

    Returns a report whose `status` is one of `passed`, `rejected`, or `unavailable`. `unavailable` is not
    a rejection: it means the checker or the recipe could not be produced, and the caller must not read it
    as "the candidate does not compile" -- the failure this project keeps re-learning.

    ``full_diagnostics`` retains the actual source excerpts needed by diagnostic-bound repairs.
    The default report still caps raw text at 16 kB; structured errors are complete in either mode.
    """
    report: dict = {"kind": "frontend-diagnostics", "status": "unavailable", "passed": None,
                    "diagnostics": "", "errors": [], "error_count": 0, "gates": {}, "target": target,
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
    if not target:
        report["reason"] = "no compiler recipe target for this function"
        return report
    try:
        selected = recipe(repo, target)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["reason"] = f"the project checker recipe is unavailable: {type(exc).__name__}: {exc}"
        return report
    if not selected.get("command"):
        report["reason"] = f"the checker recipe for {target!r} produced no command"
        return report

    with tempfile.TemporaryDirectory(prefix="decomp-frontend-") as directory:
        raw = Path(directory) / "raw.c"
        raw.write_text(source, encoding="utf-8")
        candidate = Path(directory) / "candidate.c"
        textconv, charmap = Path(repo) / "tools/textconv.py", Path(repo) / "tools/charmap.txt"
        if textconv.is_file() and charmap.is_file():
            converted = subprocess.run(["python3", str(textconv), str(charmap), str(raw), str(candidate)],
                                       cwd=repo, capture_output=True, text=True, timeout=timeout)
            if converted.returncode:
                report["reason"] = f"frontend text conversion failed: {converted.stderr[-200:]}"
                return report
        else:
            # The project converts text before compiling; without the converter the checker would read a
            # different file from the one the oracle compiles. Declining is the honest answer.
            report["reason"] = ("tools/textconv.py and tools/charmap.txt are missing, so the checker "
                                "would read a different file from the one the oracle compiles")
            return report
        try:
            process = subprocess.run([*selected["command"], str(candidate)], cwd=repo,
                                     capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            report["reason"] = f"the checker did not finish within {timeout}s"
            return report

    diagnostics = (process.stdout + process.stderr).replace(str(candidate), "candidate.c")
    errors, unparsed = [], []
    fatal = False
    for line in diagnostics.splitlines():
        text = line.strip()
        if not _ANY_ERROR.search(text):
            continue
        fatal = fatal or 'fatal error:' in text
        if _LIMIT.search(text):
            continue  # stopping notice is not another source defect
        match = _LOCATED_ERROR.match(text)
        if match:
            error = {"line": int(match.group("line")), "column": int(match.group("col")),
                     "what": match.group("what")[:160]}
            if match.group('file') != 'candidate.c':
                error['file'] = match.group('file')
            errors.append(error)
        else:
            unparsed.append(text)
    # THE ERROR LIST IS COMPLETE; THE TEXT IS WHAT GETS CUT, AND IT SAYS SO.
    #
    # This used to return `errors[:40]` with the true count beside it, which made the COUNT complete and the
    # LIST a window. Everything downstream that builds a defect-class set reads the list, so the class set
    # was still built from a window -- `eval/intake_probe._diagnostic_chain` did exactly that -- and a class
    # count is the number the next repair gets chosen against. Keeping the list complete is what makes the
    # class set an observation instead of a sample of one.
    #
    # Raw text normally keeps the last 16 kB, with the cut labeled. Repair callers can retain complete
    # real excerpts with full_diagnostics=True instead of reconstructing them from structured errors.
    report.update(status="passed" if process.returncode == 0 else "rejected",
                  passed=process.returncode == 0, returncode=process.returncode,
                  diagnostics=diagnostics if full_diagnostics else diagnostics[-16000:],
                  diagnostics_truncated=not full_diagnostics and len(diagnostics) > 16000,
                  errors=errors, error_count=len(errors),
                  unparsed_errors=unparsed,
                  # Fatal includes/preprocessing can hide the entire body.
                  # Driver failures and unmatched errors also leave an incomplete
                  # view, even when the structured list is empty.
                  errors_truncated=bool(fatal or unparsed or _LIMIT.search(diagnostics)
                                        or (process.returncode != 0 and not errors)))
    report['errors_complete'] = errors_are_complete(report)
    report["gates"] = {name: len(re.findall(pattern, diagnostics)) for name, pattern in GATES.items()
                       if re.search(pattern, diagnostics)}
    return report

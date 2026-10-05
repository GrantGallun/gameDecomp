"""Candidate-only, bounded inspection of the compiler's pre-as1 output.

The ordinary workspace scorer remains the authority for target matching. This
module asks the recorded TU compiler to compile the *active candidate* twice:
directly to an object for correspondence, then with -S for a diagnostic view.
It never reads the original TU's C file or a target source body.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from solver import byte_certificate, compiler_recipe


TIMEOUT = 45
MAX_SOURCE = 1_000_000
MAX_ASSEMBLY = 2_000_000
MAX_TEXT = 8192
_ASM_MACRO = re.compile(r"\b(?:GLOBAL_ASM|INCLUDE_ASM)\b")
_INCLUDE = re.compile(r"(?m)^\s*#\s*include\s*(.+)$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _compile_source(repo: Path, source: str) -> str:
    # score() adds the Makefile's C defines before textconv. Keep that same
    # candidate projection; it reads build settings, never reference C.
    from solver.workspace import _candidate_compile_source

    return _candidate_compile_source(repo, source)


def _direct_command(command: list[str], repo: Path, ws: Path) -> list[str]:
    """Strip a known asm-processor wrapper and make header paths cwd-stable."""
    parts = list(command)
    if len(parts) >= 4 and parts[0] in {"python3", sys.executable} and \
            parts[1].endswith("asm-processor/build.py"):
        separators = [i for i, arg in enumerate(parts) if arg == "--"]
        if len(separators) != 2 or separators[0] != 3:
            raise ValueError("unsupported asm-processor compiler recipe")
        parts = [parts[2], *parts[separators[1] + 1:]]
    executable_index = 1 if len(parts) > 1 and Path(parts[0]).name.startswith("python") else 0
    if executable_index >= len(parts) or Path(parts[executable_index]).name != "cc":
        raise ValueError("unsupported direct compiler recipe")
    if parts.count("-c") != 1 or "-S" in parts or "-o" in parts:
        raise ValueError("compiler recipe is not a compile-only command")
    executable = Path(parts[executable_index])
    if not executable.is_absolute():
        executable = repo / executable
    parts[executable_index] = str(executable)
    # The normal helper runs from repo. -I paths in its resolved recipe are
    # relative to that cwd; direct cc runs in an isolated native tempdir so
    # IDO's implicit <basename>.s output cannot land in the shared repo.
    for index, arg in enumerate(parts):
        if arg.startswith("-I") and len(arg) > 2:
            path = Path(arg[2:])
            if not path.is_absolute():
                parts[index] = "-I" + str((repo / path).resolve())
        elif arg == "-I" and index + 1 < len(parts):
            path = Path(parts[index + 1])
            if not path.is_absolute():
                parts[index + 1] = str((repo / path).resolve())
    # The ordinary snapshot lives in ws and resolves quote-includes there.
    # Put that directory first; object reproduction checks any path ambiguity.
    parts.insert(executable_index + 1, "-I" + str(ws.resolve()))
    return parts


def _invoke(command: list[str], cwd: Path) -> dict:
    record = {"command": command, "cwd": str(cwd), "timeout_seconds": TIMEOUT}
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                              errors="replace", timeout=TIMEOUT)
        record.update(returncode=proc.returncode,
                      stdout=proc.stdout[:MAX_TEXT], stderr=proc.stderr[:MAX_TEXT],
                      output_truncated=len(proc.stdout) > MAX_TEXT or len(proc.stderr) > MAX_TEXT)
    except subprocess.TimeoutExpired as exc:
        record.update(status="timeout", error=f"compiler exceeded {TIMEOUT}s")
    except OSError as exc:
        record.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:400]}")
    return record


def _correspondence(ordinary: Path | None, direct: Path, ws: Path, source: str) -> dict:
    if ordinary is None:
        return {"status": "unavailable", "reason": "ordinary candidate object was not supplied"}
    ordinary = Path(ordinary).resolve()
    if ordinary.suffix != ".o" or ordinary.name == "target.o" or not ordinary.is_relative_to(ws.resolve()):
        return {"status": "unavailable", "reason": "ordinary candidate object must be inside the workspace"}
    if not ordinary.is_file():
        return {"status": "unavailable", "reason": "ordinary candidate object is missing"}
    raw, other = direct.read_bytes(), ordinary.read_bytes()
    result = {"direct_sha256": _sha(raw), "ordinary_sha256": _sha(other)}
    if raw == other:
        return {**result, "status": "raw_object_equal"}
    # The normal helper removes .mdebug. Compare allocated bytes and relocation
    # expressions by the same certificate used by the scorer, but retain only a
    # small receipt and never call this target exactness.
    receipt = byte_certificate.certify(ordinary, direct, source=source)
    return {**result, "status": "object_sections_equal" if receipt.get("exact") else "different",
            "certificate_status": receipt.get("status"),
            "certificate_error": receipt.get("error", "")[:400]}


def inspect(repo: Path, ws: Path, function: str, source: str, output: Path, *,
            hypothesis: str, object_path: Path | None = None) -> dict:
    """Return a serializable direct-compiler observation, never a target verdict.

    ``output`` is an artifact directory; the full bounded assembly is saved
    there. A missing or differing ordinary object leaves the capture explicitly
    diagnostic only. Each compiler invocation has a 45-second limit.
    """
    repo, ws, output = Path(repo).resolve(), Path(ws).resolve(), Path(output)
    report = {"kind": "compiler-phase-experiment", "schema_version": 1,
              "function": function, "hypothesis": hypothesis[:512],
              "source_sha256": _sha(source.encode()), "status": "unavailable",
              "comparable": False, "compile_units": 0, "invocations": [],
              "reproduction": {"status": "unavailable"}}
    if len(source.encode()) > MAX_SOURCE:
        report["reason"] = "candidate source exceeds inspection limit"
        return report
    if _ASM_MACRO.search(source):
        report["reason"] = "candidate requires asm-processor transformation"
        return report
    if not re.fullmatch(r"[A-Za-z_]\w*", function):
        report["reason"] = "invalid function identity"
        return report
    for directive in _INCLUDE.findall(source):
        include = re.match(r'[<"]([^>"]+)[>"]', directive.strip())
        if not include:
            report["reason"] = "unresolved candidate include could read source outside headers"
            return report
        name = include.group(1)
        if (Path(name).suffix not in {".h", ".inc"} or ".." in name.replace("\\", "/").split("/")
                or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name)):
            report["reason"] = "candidate include is outside permitted headers"
            return report
    try:
        identity = json.loads((ws / ".compiler-target.json").read_text())
        if identity.get("function") != function or not identity.get("target"):
            raise ValueError("workspace compiler identity does not match function")
        recipe = compiler_recipe.resolve(repo, identity["target"])
        if recipe.get("target") != identity["target"]:
            raise ValueError("resolved compiler target changed")
        direct = _direct_command(recipe["command"], repo, ws)
        report["recipe"] = {key: recipe.get(key) for key in
                            ("target", "makefile_sha256", "projection_sha256", "source_origin")}
        report["recipe"]["command_sha256"] = _sha(json.dumps(recipe["command"]).encode())
        compiled_source = _compile_source(repo, source)
        textconv, charmap = repo / "tools/textconv.py", repo / "tools/charmap.txt"
        if not textconv.is_file() or not charmap.is_file():
            raise ValueError("project text conversion is unavailable")
        temp_base = Path("/tmp") if os.name == "posix" else None
        with tempfile.TemporaryDirectory(prefix="decomp-phase-", dir=temp_base) as temp:
            scratch = Path(temp)
            raw, candidate = scratch / "raw.c", scratch / "candidate.c"
            raw.write_text(compiled_source)
            conversion = subprocess.run([sys.executable, str(textconv), str(charmap),
                                         str(raw), str(candidate)], cwd=scratch,
                                        capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
            if conversion.returncode:
                raise ValueError("project text conversion failed: " + conversion.stderr[:400])
            report["compiled_source_sha256"] = _sha(candidate.read_bytes())
            object_file = scratch / "candidate.o"
            object_command = [*direct, "-o", str(object_file), str(candidate)]
            object_run = _invoke(object_command, scratch)
            report["invocations"].append(object_run)
            report["compile_units"] += 1
            if object_run.get("returncode") != 0 or not object_file.is_file():
                report.update(status="error", reason="direct object compile failed or produced no object")
                return report
            report["reproduction"] = _correspondence(object_path, object_file, ws, compiled_source)
            phase_command = [arg for arg in direct if arg != "-c"] + ["-S", str(candidate)]
            phase_run = _invoke(phase_command, scratch)
            report["invocations"].append(phase_run)
            report["compile_units"] += 1
            phase_file = scratch / "candidate.s"
            if phase_run.get("returncode") != 0 or not phase_file.is_file():
                report.update(status="error", reason="direct -S compile failed or produced no assembly")
                return report
            size = phase_file.stat().st_size
            if size > MAX_ASSEMBLY:
                report.update(status="unavailable", reason="phase assembly exceeds inspection limit")
                return report
            assembly = phase_file.read_bytes()
            output.mkdir(parents=True, exist_ok=True)
            artifact = output / (function + "-" + report["source_sha256"][:16] + ".pre-as1.s")
            if artifact.exists() and artifact.read_bytes() != assembly:
                raise ValueError("phase artifact changed for the same source identity")
            artifact.write_bytes(assembly)
            report["assembly"] = {"path": str(artifact.resolve()), "sha256": _sha(assembly),
                                  "bytes": size, "text": assembly.decode("utf-8", "replace")[:MAX_TEXT],
                                  "text_truncated": size > MAX_TEXT}
            report["status"] = "captured"
            report["comparable"] = report["reproduction"]["status"] in {
                "raw_object_equal", "object_sections_equal"}
            return report
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        report.update(status="unavailable", reason=f"{type(exc).__name__}: {str(exc)[:400]}")
        return report

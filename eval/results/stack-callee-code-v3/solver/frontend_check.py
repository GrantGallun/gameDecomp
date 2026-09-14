"""Run the project's strict C policy on a candidate, never reference C bodies.

This is an isolated-candidate check, not full-TU or ROM certification. Missing
TU-local context is reported, not silently supplied from the reference source.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from solver import compiler_recipe

FIELDS = ("CC_CHECK", "CC_CHECK_FLAGS", "CC_CHECK_WARNINGS", "CC_CHECK_INCLUDES",
          "C_DEFINES", "CC_CHECK_MIPS_DEFINES")


def projection(raw: str, target: str) -> str:
    # The supported default policy is explicitly WERROR=0. Selected pointer,
    # prototype and return diagnostics remain errors via their -Werror= flags.
    block = r"(?m)^ifneq\s*\(\$\(WERROR\),0\)\s*\n[ \t]*CC_CHECK_WARNINGS\s*\+=\s*-Werror\s*\nendif\s*$"
    if not re.search(r"(?m)^WERROR\s*\?=\s*0\s*$", raw) or len(re.findall(block, raw)) != 1:
        raise ValueError("unsupported frontend warning policy")
    raw = re.sub(block, "", raw)
    return compiler_recipe.projection(raw, target, "mips-linux-gnu-", fields=FIELDS)


def recipe(repo_string: str, raw: str, target: str) -> dict:
    projected = projection(raw, target)
    with tempfile.TemporaryDirectory(prefix="decomp-check-recipe-") as directory:
        path = Path(directory) / "check.mk"
        path.write_text(projected)
        result = subprocess.run(["make", "--no-print-directory", "-rR", "-f", str(path), target],
            cwd=repo_string, capture_output=True, text=True, timeout=30,
            env={**os.environ, "MAKEFLAGS": "", "MFLAGS": "", "MAKEOVERRIDES": "", "WERROR": "0"})
    values = dict(re.findall(r"(?m)^__DECOMP_(\w+)__=(.*)$", result.stdout))
    if result.returncode or set(values) != set(FIELDS) or values["CC_CHECK"] != "clang":
        raise ValueError("unsupported or unresolved frontend checker")
    binary = shutil.which("clang")
    if not binary:
        raise ValueError("project C checker unavailable")
    command = [binary]
    for key in FIELDS[1:]:
        command += shlex.split(values[key])
    if "-fsyntax-only" not in command:
        raise ValueError("checker is not syntax-only")
    command += ["-fno-color-diagnostics", "-iquote", str(Path(target[6:]).parent)]
    return {"command": command, "settings": values, "target": target,
            "makefile_sha256": compiler_recipe.sha(raw.encode()),
            "checker_sha256": compiler_recipe.sha(Path(binary).read_bytes()),
            "warning_policy": "project default WERROR=0; explicit error classes preserved"}


def check(repo: Path, source: Path, target: str) -> dict:
    report = {"kind": "project-policy-candidate-frontend", "passed": None,
              "source_sha256": compiler_recipe.sha(source.read_bytes()),
              "scope": "candidate and included headers; not full TU or whole-ROM verification"}
    try:
        if os.environ.get("WERROR", "0") != "0":
            raise ValueError("nondefault WERROR environment requires explicit policy")
        selected = recipe(str(repo), (repo / "Makefile").read_text(), target)
        # Text conversion follows the project rule before checking.
        with tempfile.TemporaryDirectory(prefix="decomp-frontend-", dir=source.parent) as directory:
            converted = Path(directory) / "candidate.c"
            conversion = subprocess.run(["python3", "tools/textconv.py", "tools/charmap.txt",
                str(source), str(converted)], cwd=repo, capture_output=True, text=True, timeout=30)
            if conversion.returncode:
                raise ValueError("frontend text conversion failed: " + conversion.stderr[-1000:])
            process = subprocess.run([*selected["command"], str(converted)], cwd=repo,
                capture_output=True, text=True, timeout=60)
            diagnostic = (process.stdout + process.stderr).replace(str(converted), "candidate.c")
        report.update(passed=process.returncode == 0, status="passed" if process.returncode == 0 else "rejected",
                      returncode=process.returncode, diagnostics=diagnostic[-16000:], recipe=selected)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report.update(status="unavailable", diagnostics=str(exc))
    source.with_suffix(".frontend.json").write_text(json.dumps(report, indent=2) + "\n")
    return report

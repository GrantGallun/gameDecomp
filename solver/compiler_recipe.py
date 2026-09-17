"""Resolve the game's C compiler settings without reading reference C bodies.

GNU make evaluates a dependency-free, assignment-only projection of the build
configuration. No original build recipes execute. The existing matching helper
is copied with ONLY its compiler invocation changed; all guards and verifiers
remain intact. Unsupported postprocessing is a visible blocker, never ignored.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


FIELDS = ("IDO_CC", "CFLAGS", "C_OPT", "C_MIPS", "ASFLAGS", "C_OBJ_POSTPROCESS")
INVOCATION = 'python3 "$ASM_PROC" "$CC" -- "$AS" "${ASFLAGS[@]}" -- "${CFLAGS[@]}" -o "$OBJECT_OUTPUT" "$SOURCE_SNAPSHOT"'
REFERENCE = re.compile(r"\$\(([A-Za-z_]\w*)\)")


class ObjectBackendRequired(ValueError):
    """A resolved build requirement, not authorization to execute its command."""
    def __init__(self,target,settings,makefile_sha256,projection_sha256):
        super().__init__('TU requires a separate object postprocessing backend')
        self.evidence={'kind':'object-postprocessing-backend-required','target':target,
            'postprocess':settings['C_OBJ_POSTPROCESS'],'assembler_flags':settings['ASFLAGS'],
            'makefile_sha256':makefile_sha256,'projection_sha256':projection_sha256,
            'owner':'solver/compiler_recipe.py','command_executed':False,
            'next_action':'Inspect postprocessor and candidate ELF prerequisites; implement isolated object handling with before/after receipts, preserving build STOP rules.'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def projection(makefile: str, target: str, cross: str, fields: tuple = FIELDS) -> str:
    if not re.fullmatch(r"build/src/[A-Za-z0-9_./-]+\.o", target) or ".." in Path(target).parts:
        raise ValueError("unsupported TU object identity")
    logical = re.sub(r"\\\r?\n[ \t]*", " ", makefile)
    assignments = []
    conditional_depth = 0
    conditional_names = set()
    for line in logical.splitlines():
        if line.startswith("\t"):
            continue
        if re.match(r"\s*(?:ifeq|ifneq|ifdef|ifndef)\b", line):
            conditional_depth += 1
        elif re.match(r"\s*endif\b", line):
            conditional_depth -= 1
        match = re.fullmatch(r"(?:(.+?):\s*)?([A-Za-z_]\w*)\s*([:+?]?=)\s*(.*?)\s*", line)
        if match:
            selector, name, op, value = match.groups()
            assignments.append((selector, name, op, value))
            if conditional_depth:
                conditional_names.add(name)
    needed = set(fields)
    selected = []
    while True:
        selected = [row for row in assignments if row[1] in needed and row[1] != "CROSS"]
        dependencies = {name for selector, _, _, value in selected
                        for name in REFERENCE.findall((selector or "") + " " + value)}
        if dependencies <= needed:
            break
        needed |= dependencies
    defined = {row[1] for row in selected} | {"CROSS"}
    if (needed & conditional_names) - {"CROSS"}:
        raise ValueError("conditional compiler configuration requires a richer resolver")
    if needed - defined:
        raise ValueError("undefined build variables: " + ", ".join(sorted(needed - defined)))
    lines = [f"CROSS := {cross}"]
    for selector, name, op, value in selected:
        # Only simple variable references and $@ survive the projection. Reject
        # $(shell ...), $(eval ...), escaped recipes, includes and functions.
        for text in (selector or "", value):
            stripped = REFERENCE.sub("", text).replace("$@", "")
            if "$" in stripped or any(c in stripped for c in "\n\r;`#"):
                # ':' is a valid no-op postprocessor, but shell operators are
                # never executed here; nontrivial postprocessors fail below.
                raise ValueError("unsupported expansion in compiler configuration")
        prefix = selector + ": " if selector else ""
        lines.append(f"{prefix}{name} {op} {value}")
    lines += [f".PHONY: {target}", f"{target}:"]
    lines += [f"\t@$(info __DECOMP_{key}__=$({key})) :" for key in fields]
    return "\n".join(lines) + "\n"


@lru_cache(maxsize=512)
def _resolve(repo_string: str, raw_makefile: str, target: str, cross: str) -> dict:
    repo = Path(repo_string)
    projected = projection(raw_makefile, target, cross)
    with tempfile.TemporaryDirectory(prefix="decomp-recipe-") as temporary:
        path = Path(temporary) / "recipe.mk"
        path.write_text(projected)
        process = subprocess.run(["make", "--no-print-directory", "-rR", "-f", str(path), target],
            cwd=repo, capture_output=True, text=True, timeout=30,
            # Do not inherit user make flags that select a different compiler.
            env={**os.environ, "MAKEFLAGS": "", "MFLAGS": "", "MAKEOVERRIDES": ""})
    if process.returncode:
        raise ValueError("compiler configuration resolution failed: " + process.stderr[-1000:])
    values = dict(re.findall(r"(?m)^__DECOMP_(\w+)__=(.*)$", process.stdout))
    if set(values) != set(FIELDS):
        raise ValueError("incomplete compiler configuration")
    if values["C_OBJ_POSTPROCESS"].strip() != ":" or "-no-pad-sections" in values["ASFLAGS"]:
        raise ObjectBackendRequired(target,values,sha(raw_makefile.encode()),sha(projected.encode()))
    # The project's long-long runtime TU explicitly selects MIPS III with
    # 32-bit ABI slots. Admit that pair, not a bare ISA change whose ABI is
    # unspecified. The command and independent object checks remain intact.
    if values["C_MIPS"] not in {"-mips1", "-mips2", "-mips3 -32"}:
        raise ValueError("unsupported compiler ISA/ABI recipe: " + values["C_MIPS"])
    command = shlex.split(values["IDO_CC"]) + shlex.split(values["CFLAGS"]) + shlex.split(values["C_OPT"])
    # Source-local header search is part of the real generic C rule. Only .h
    # files are inputs; no reference C body is passed to this command.
    command += ["-I" + str(Path(target[6:]).parent)]
    compiler = "tools/ido-recomp/linux/cc"
    if command[0] not in {"python3", compiler} or compiler not in command:
        raise ValueError("unsupported compiler command")
    return {"schema_version": 1, "target": target, "settings": values, "command": command,
            "makefile_sha256": sha(raw_makefile.encode()), "projection_sha256": sha(projected.encode()),
            "authority": "existing project build configuration, not inferred original compiler settings",
            "source_origin": "C TU" if (repo / (target[6:-2] + ".c")).is_file()
                else "assembly TU; C reconstruction experiment, not original C recipe",
            "scope": "C code generation; full TU syntax checking and final ROM integration are separate"}


def resolve(repo: Path, target: str) -> dict:
    cross = next((name for name in ("mips-linux-gnu-", "mips64-linux-gnu-", "mips64-elf-")
                  if shutil.which(name + "as")), None)
    if not cross:
        raise ValueError("no supported MIPS toolchain")
    return _resolve(str(repo.resolve()), (repo / "Makefile").read_text(), target, cross)


def adapt_helper(helper: str, recipe: dict) -> str:
    if helper.count(INVOCATION) != 1:
        raise ValueError("matching helper changed; refusing an unverified compiler adapter")
    command = "DECOMP_CC=(" + shlex.join(recipe["command"]) + ")\n"
    command += '"${DECOMP_CC[@]}" -o "$OBJECT_OUTPUT" "$SOURCE_SNAPSHOT"'
    return helper.replace(INVOCATION, command)


def prepare(repo: Path, ws: Path, conn, function: str) -> tuple[Path, dict] | None:
    """Called under the workspace scoring lock; no shared helper is edited."""
    helper = ws / "build.sh"
    if not helper.is_file() or not (repo / "Makefile").is_file():
        return None
    identity_path = ws / ".compiler-target.json"
    if conn is not None and function:
        row = conn.execute("SELECT t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?",
                           (function,)).fetchone()
        if row is None:
            raise ValueError("missing function-to-TU compiler identity")
        identity = {"function": function, "target": row[0]}
        if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
            raise ValueError("workspace compiler target identity changed")
    elif identity_path.exists():
        identity = json.loads(identity_path.read_text())
    else:
        return None  # legacy unconfigured workspace; explicit in Attempt
    recipe = resolve(repo, identity["target"])
    original = helper.read_text()
    recipe = {**recipe, "helper_sha256": sha(original.encode())}
    text = adapt_helper(original, recipe)
    key = sha(json.dumps(recipe, sort_keys=True).encode())[:20]
    script = ws / f".compiler-{key}.sh"
    manifest = ws / f".compiler-{key}.json"
    for path, data in ((script, text), (manifest, json.dumps(recipe, indent=2) + "\n")):
        if path.exists():
            if path.read_text() != data:
                raise ValueError("compiler artifact changed outside controller")
        else:
            path.write_text(data)
    # SUPERSEDED ARTIFACTS ARE DELETED, not left behind. Two consumers pick a script by GLOB rather
    # than by the recipe key they hold -- `solver.uopt_diagnosis._recipe_command` takes the first
    # `.compiler-*.json` in sorted order -- so a stale copy of the helper stays reachable after the
    # helper changes. That is how a removed policy can keep being enforced by an old file: the 2,015
    # per-workspace `.compiler-*.sh` each carried their own copy of the `do`-token refusal
    # (2026-09-17), and stripping the helper alone would not have reached them.
    for stale in tuple(ws.glob(".compiler-*.sh")) + tuple(ws.glob(".compiler-*.json")):
        if stale not in (script, manifest):
            stale.unlink(missing_ok=True)
    if not identity_path.exists():
        identity_path.write_text(json.dumps(identity, sort_keys=True) + "\n")
    return script, {**recipe, "script": str(script), "manifest": str(manifest)}

"""Apply explicitly prepared file replacements in isolation and verify a ROM.

This is an operator-supplied integration manifest, NOT an LLM command channel.
It never changes the working game repository. A matching ROM with assembly
fallbacks is integration evidence, not a completed C decompilation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inside(root: Path, name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("integration paths must be relative and contained")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise ValueError("integration path escapes workspace")
    return resolved


def compare_roms(target: bytes, candidate: bytes) -> dict:
    first = next((i for i, (a, b) in enumerate(zip(target, candidate)) if a != b), None)
    if first is None and len(target) != len(candidate):
        first = min(len(target), len(candidate))
    return {"whole_rom_verified": bool(target) and target == candidate,
            "target_sha256": sha(target), "candidate_sha256": sha(candidate),
            "target_bytes": len(target), "candidate_bytes": len(candidate),
            "first_difference_offset": first}


def run(*, repo: Path, manifest: Path, output: Path, staging_parent: Path | None = None,
        timeout: int = 1200) -> dict:
    archived_log = output.with_suffix(".build.log")
    archived_rom = output.with_suffix(".rebuilt.z64")
    if any(path.exists() for path in (output, archived_log, archived_rom)):
        raise ValueError("refusing to overwrite an integration receipt")
    spec_bytes = manifest.read_bytes()
    spec = json.loads(spec_bytes)
    replacements = spec.get("replacements", [])
    if not replacements:
        raise ValueError("explicit prepared source/header replacements are required")
    # Freeze bytes before any external process; paths are relative to manifest.
    prepared, seen = [], set()
    for item in replacements:
        destination = inside(repo, item["path"])
        if destination.suffix not in {".c", ".h"} or item["path"] in seen:
            raise ValueError("only distinct C/header replacements are permitted")
        seen.add(item["path"])
        if sha(destination.read_bytes()) != item["base_sha256"]:
            raise ValueError("integration base changed: " + item["path"])
        source = inside(manifest.parent, item["replacement"]).read_bytes()
        if sha(source) != item["replacement_sha256"]:
            raise ValueError("prepared source changed: " + item["path"])
        prepared.append((item["path"], source))
    reference = inside(repo, spec["reference_rom"]).read_bytes()
    if not reference or sha(reference) != spec["reference_sha256"]:
        raise ValueError("reference ROM identity mismatch")
    if inside(repo, spec["built_rom"]).suffix.lower() not in {".z64", ".n64", ".v64", ".rom", ".bin"}:
        raise ValueError("built artifact must be a ROM image")
    stage = Path(tempfile.mkdtemp(prefix="decomp-integration-", dir=staging_parent))
    work = stage / "game"
    receipt = {"kind": "isolated-rom-integration", "schema_version": 1,
               "status": "running", "manifest_sha256": sha(spec_bytes),
               "workspace": str(work), "whole_rom_verified": False,
               "complete_c_decompilation": False,
               "scope": "explicit replacements and rebuilt whole ROM; not all-C completion",
               "replacements": [{"path": name, "sha256": sha(data)} for name, data in prepared]}
    try:
        shutil.copytree(repo, work, symlinks=True, ignore=shutil.ignore_patterns(
            ".git", "nonmatchings", "build", ".venv", "__pycache__", ".cache",
            ".agents", ".codex", ".claude", "AGENTS.md"))
        # Refuse links, including tools, rather than permit a build to write
        # through a copied symlink into the user's working tree.
        if any(p.is_symlink() for p in work.rglob("*")):
            raise ValueError("isolated build refuses symlinks; vendor required inputs first")
        for name, data in prepared:
            destination = inside(work, name)
            original = next(item for item in replacements if item["path"] == name)
            if sha(destination.read_bytes()) != original["base_sha256"]:
                raise ValueError("integration base changed during snapshot")
            destination.write_bytes(data)
        built = inside(work, spec["built_rom"])
        if built.exists():
            # Only a validated artifact in the newly created disposable copy.
            built.unlink()
        log = stage / "build.log"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        # Installed Python tools are reused, not copied through symlinks. Build
        # source/output paths still point exclusively into the isolated tree.
        if (repo / ".venv/bin/python3").exists():
            env["PATH"] = str(repo / ".venv/bin") + os.pathsep + env.get("PATH", "")
        receipt["tool_environment"] = {"python_bin": env["PATH"].split(os.pathsep)[0]}
        with log.open("wb") as stream:
            process = subprocess.run(["bash", "./tools/build-and-verify.sh"], cwd=work,
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, env=env)
        receipt.update(build_returncode=process.returncode, build_log=str(log))
        if process.returncode:
            receipt["status"] = "build_failed"
        elif not built.is_file():
            receipt["status"] = "missing_rom"
        else:
            receipt.update(compare_roms(reference, built.read_bytes()))
            receipt["status"] = "rom_exact" if receipt["whole_rom_verified"] else "rom_mismatch"
        receipt["remaining_assembly_markers"] = sum(
            len(re.findall(rb"\b(?:INCLUDE_ASM|GLOBAL_ASM)\b", p.read_bytes()))
            for p in (work / "src").rglob("*.c"))
        receipt["assembly_marker_caveat"] = "textual count, not a linked function inventory"
    except Exception as exc:
        receipt.update(status="error", error=f"{type(exc).__name__}: {exc}")
    output.parent.mkdir(parents=True, exist_ok=True)
    # WSL/container temporary directories may disappear between invocations.
    # Preserve the actual build log/image with the durable receipt, not just
    # their hashes and a soon-to-expire scratch path.
    log = stage / "build.log"
    if log.is_file():
        shutil.copyfile(log, archived_log)
        receipt["build_log"] = str(archived_log.resolve())
    if receipt.get("build_returncode") == 0:
        built = inside(work, spec["built_rom"])
        if built.is_file():
            shutil.copyfile(built, archived_rom)
            receipt["built_rom_artifact"] = str(archived_rom.resolve())
    receipt["workspace_caveat"] = "scratch tree may be ephemeral; log and built ROM are archived beside receipt"
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("repo", "manifest", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--staging-parent", type=Path)
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps(result, indent=2))
    return 0 if result["whole_rom_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

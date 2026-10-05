"""Run a decomp repository's own asset extraction, and take back only the generated headers.

Why this exists. Some repositories cannot yield training pairs without a step that reads the ROM:
DKR generates `include/asset_enums.h` from the asset table, so 40 of its 47 source files fail to
compile without it. The grabber refuses to run repository code, and that refusal is right -- DKR's
Makefile downloads binutils from GNU.org during PARSING, so even `make -n` hits the network. This
module runs that step anyway, but only inside a sandbox that makes the refusal's concerns moot.

Four guarantees, each enforced in code and each visible in the receipt:

1. PINNED ROM. The dump's sha1 must equal the hash the repository publishes for the variant being
   built, checked here before anything runs and again by the tool itself. A ROM is operator-supplied
   and is never downloaded.
2. THROWAWAY COPY. Work happens in a fresh `git clone` of the pinned commit under the sandbox root.
   The corpus checkout is masked with a tmpfs inside the sandbox, so the extraction cannot write to
   the tree the grabber compiles from even by accident.
3. NO NETWORK. Every command runs under `bwrap --unshare-net`, so `get-binutils.sh`, a stray `curl`,
   or anything else the build reaches for fails instead of succeeding quietly.
4. ONLY DECLARED OUTPUTS COME BACK. Nothing is copied out of the sandbox except paths matching the
   recipe's `outputs` globs, which must stay inside the sandbox root. Everything else is discarded
   with the work directory.

A repository's dependencies are a separate, explicitly labelled phase: pip installs from the
repository's own pinned `requirements.txt`, and `apt-get download` of named -dev packages extracted
into a local sysroot. That phase needs the network and is the only part of this module that does.
It runs no repository code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from tools import corpus_grabber as cg

SANDBOX_ROOT = Path.home() / "decomp" / "sandbox"
GENERATED_ROOT = cg.GENERATED_ROOT      # one definition, so the grabber and this agree by construction
VENV = Path.home() / "decomp" / "sandbox-venv"
SYSROOT = Path.home() / "decomp" / "sysroot"
BWRAP = "bwrap"
DEV_LIB = "usr/lib/x86_64-linux-gnu"
STAMP = "%Y%m%dT%H%M%S"


class Refused(RuntimeError):
    """A guarantee declined. Never caught silently: it ends the run with its reason."""


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- guarantee 1: the ROM is the pinned revision ------------------------------

def verify_rom(rom: Path, expected_sha1: str) -> str:
    """Refuse anything but the exact dump the repository pins for this variant."""
    if not rom.is_file():
        raise Refused(f"rom not found: {rom}")
    digest = _sha1(rom)
    if digest != expected_sha1:
        raise Refused(f"rom {rom.name} is {digest}, but this variant pins {expected_sha1}")
    return digest


# --- guarantee 3: no network --------------------------------------------------

def bwrap_argv(work: Path, mask: tuple[Path, ...] = ()) -> list[str]:
    """The isolation every extraction command runs under.

    `--unshare-net` is the whole point: without it DKR's build reaches for GNU.org while make is
    still parsing. `--ro-bind / /` makes the host read-only and the explicit `--bind` re-opens only
    the throwaway directory; each masked path becomes an empty tmpfs so the sandbox cannot see the
    pristine corpus checkout at all.
    """
    argv = [BWRAP, "--unshare-net", "--die-with-parent", "--new-session",
            "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
            "--bind", str(work), str(work)]
    for path in mask:
        argv += ["--tmpfs", str(path)]
    argv += ["--chdir", str(work), "--setenv", "HOME", str(work)]
    return argv


def mask_paths(exists=Path.exists) -> tuple[Path, ...]:
    """What the sandbox must not be able to see: the evaluation sources, and the corpus checkout.

    The throwaway clone is made from the corpus checkout BEFORE the sandbox starts, so hiding it
    during the run costs nothing and means an extraction cannot write to the tree the grabber
    compiles from. `throwaway` would otherwise be a claim rather than a property. Only paths that
    exist are returned: bwrap cannot mount a tmpfs over nothing.
    """
    return tuple(path for path in (*cg.EVALUATION_SOURCES, cg.CORPUS_DIR) if exists(path))


def _run(argv: list[str], *, timeout: int, cwd: Path | None = None) -> dict:
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        code, out, err, timed_out = None, exc.stdout or "", exc.stderr or "", True
    return {"argv": argv, "exit": code, "timed_out": timed_out,
            "stdout_tail": (out or "")[-2000:], "stderr_tail": (err or "")[-2000:]}


# --- guarantee 4: only declared outputs come back -----------------------------

def resolve_outputs(work: Path, patterns: list[str]) -> list[Path]:
    """Files matching `patterns` under `work`. A pattern reaching outside is refused.

    `..` in a glob is the one way a repository could ask this module to hand back something it was
    never allowed to read, so it is rejected before any copy happens.
    """
    root = work.resolve()
    found: list[Path] = []
    for pattern in patterns:
        if ".." in Path(pattern).parts:
            raise Refused(f"output pattern escapes the sandbox: {pattern}")
        for path in sorted(work.glob(pattern)):
            if path.is_file() and root in path.resolve().parents:
                found.append(path)
    return found


def collect(work: Path, patterns: list[str], destination: Path) -> list[dict]:
    """Copy declared outputs into `destination`, preserving their repo-relative path."""
    root = work.resolve()
    copied = []
    for path in resolve_outputs(work, patterns):
        relative = path.resolve().relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append({"path": relative.as_posix(), "bytes": target.stat().st_size,
                       "sha256": _sha256(target)})
    return copied


# --- dependency phase (the only part that may use the network) -----------------

def ensure_python_env(requirements: Path | None, venv: Path, receipt: dict) -> Path:
    """A venv built from the repository's pinned requirements. No repository code runs."""
    python = venv / "bin" / "python3"
    entry = {"venv": str(venv), "requirements": str(requirements) if requirements else None}
    if python.exists():
        entry["action"] = "reused"
        receipt["dependencies"].append(entry)
        return python
    venv.parent.mkdir(parents=True, exist_ok=True)
    _run(["python3", "-m", "venv", str(venv)], timeout=600)
    if requirements and requirements.is_file():
        step = _run([str(venv / "bin" / "pip"), "install", "-q", "--disable-pip-version-check",
                     "-r", str(requirements)], timeout=1800)
        entry["pip_exit"] = step["exit"]
        if step["exit"] != 0:
            raise Refused(f"pip install failed: {step['stderr_tail'][-400:]}")
    entry["action"] = "created"
    receipt["dependencies"].append(entry)
    if not python.exists():
        raise Refused(f"no python at {python}")
    return python


def ensure_dev_packages(packages: list[str], sysroot: Path, receipt: dict) -> dict:
    """`apt-get download` + `dpkg-deb -x` into a local sysroot: a dev package without root.

    A -dev package ships the linker name (`libpcre2-8.so`) as a symlink whose target lives in the
    runtime package that is already installed. Extracting alone leaves it dangling, so a dangling
    symlink is repointed at the system's real library and the repair is recorded.
    """
    libdir = sysroot / DEV_LIB
    entry = {"packages": packages, "sysroot": str(sysroot), "repaired_links": [], "skipped": []}
    for package in packages:
        marker = sysroot / f".{package}.ok"
        if marker.exists():
            entry["skipped"].append(package)
            continue
        with tempfile.TemporaryDirectory(prefix="deb-") as tmp:
            download = _run(["apt-get", "download", package], timeout=600, cwd=Path(tmp))
            if download["exit"] != 0:
                raise Refused(f"apt-get download {package} failed: {download['stderr_tail'][-300:]}")
            debs = sorted(Path(tmp).glob("*.deb"))
            if not debs:
                raise Refused(f"apt-get download {package} produced no .deb")
            for deb in debs:
                extract = _run(["dpkg-deb", "-x", str(deb), str(sysroot)], timeout=600)
                if extract["exit"] != 0:
                    raise Refused(f"dpkg-deb -x {deb.name} failed")
        marker.write_text("ok\n", encoding="utf-8")
    for link in sorted(libdir.glob("*.so")):
        if link.resolve().exists():
            continue
        for candidate in sorted(Path("/usr/lib").glob(f"*/{link.name}.*")):
            link.unlink()
            link.symlink_to(candidate)
            entry["repaired_links"].append(f"{link.name} -> {candidate}")
            break
    receipt["dependencies"].append(entry)
    return {"include": sysroot / "usr" / "include", "lib": libdir}


# --- the run ------------------------------------------------------------------

def load_extraction(selector: str) -> dict:
    """The recipe for `selector`, joined with its corpus_grabber entry, requiring `extraction`."""
    entry = cg.load_recipes()[selector]
    if "extraction" not in entry:
        raise Refused(f"{selector} declares no extraction step; nothing to run")
    return entry


def substitute(values: list[str], *, venv: Path, dev: dict | None) -> list[str]:
    """Fill `{python}`, `{dev_include}`, `{dev_lib}` in a declared command."""
    mapping = {"{python}": str(venv / "bin" / "python3"),
               "{dev_include}": str(dev["include"]) if dev else "",
               "{dev_lib}": str(dev["lib"]) if dev else "",
               "{venv}": str(venv)}
    out = []
    for value in values:
        for key, replacement in mapping.items():
            value = value.replace(key, replacement)
        out.append(value)
    return out


def extract(selector: str, *, rom_override: Path | None = None, keep: bool = False,
            sandbox_root: Path = SANDBOX_ROOT, generated_root: Path = GENERATED_ROOT,
            receipt: dict | None = None) -> dict:
    """Run one repository's extraction in a sandbox and collect its declared outputs."""
    entry = load_extraction(selector)
    plan = entry["extraction"]
    receipt = receipt if receipt is not None else {}
    receipt.update({
        "selector": selector, "repository": entry["id"], "variant": entry.get("variant"),
        "commit": entry["commit"], "started_at": int(time.time()),
        "guarantees": {"network_denied": True, "throwaway_copy": True,
                       "declared_outputs_only": True, "rom_pinned": True},
        "dependencies": [], "steps": [], "outputs": [],
    })

    rom = Path(rom_override) if rom_override else cg.ROOT / plan["rom_file"]
    expected = entry["rom"]["pinned_sha1"] if "rom" in entry else plan["rom_sha1"]
    receipt["rom"] = {"path": str(rom), "sha1": verify_rom(rom, expected), "expected": expected}

    python = None
    if plan.get("python_requirements"):
        # Read the pinned requirements from the corpus checkout, never from this project.
        requirements = cg.CORPUS_DIR / entry["id"] / plan["python_requirements"]
        python = ensure_python_env(requirements, VENV, receipt)
    dev = ensure_dev_packages(plan.get("apt_dev_packages", []), SYSROOT, receipt) \
        if plan.get("apt_dev_packages") else None
    if python is None:
        python = VENV / "bin" / "python3"

    stamp = time.strftime(STAMP, time.gmtime())
    work = sandbox_root / f"{selector.replace('@', '-')}-{stamp}"
    if work.exists():
        raise Refused(f"sandbox already exists: {work}")
    work.parent.mkdir(parents=True, exist_ok=True)
    checkout = work / "repo"
    _run(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout",
          str(cg.CORPUS_DIR / entry["id"]), str(checkout)], timeout=1800)
    _run(["git", "-C", str(checkout), "checkout", "--quiet", "--detach", entry["commit"]], timeout=600)
    if _run(["git", "-C", str(checkout), "rev-parse", "HEAD"], timeout=120)["stdout_tail"].strip() \
            != entry["commit"]:
        raise Refused(f"{selector}: throwaway checkout is not the pinned commit")

    rom_target = checkout / plan["rom_destination"]
    rom_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rom, rom_target)
    receipt["throwaway"] = str(work)

    # Masked inside the sandbox: the evaluation sources it could otherwise read, and the corpus
    # checkout it was cloned from -- so an extraction cannot write to the tree the grabber compiles.
    # The only writable path is the throwaway checkout itself; not even the sandbox root is exposed.
    isolate = bwrap_argv(checkout, mask_paths())

    for step in plan.get("build", []):
        argv = list(isolate) + substitute(step["command"], venv=VENV, dev=dev)
        if step.get("cwd"):
            argv = argv[:argv.index("--chdir")] + ["--chdir", str(checkout / step["cwd"])] \
                + argv[argv.index("--chdir") + 2:]
        record = _run(argv, timeout=step.get("timeout", 1800))
        record["name"] = step.get("name", "build")
        receipt["steps"].append(record)
        if record["exit"] != 0:
            raise Refused(f"{selector}: {record['name']} failed (exit {record['exit']}, "
                          f"timed_out={record['timed_out']}): {record['stderr_tail'][-400:]}")

    for step in plan["commands"]:
        argv = list(isolate) + substitute(step, venv=VENV, dev=dev)
        record = _run(argv, timeout=plan.get("timeout", 3600))
        record["name"] = Path(step[0]).name
        receipt["steps"].append(record)
        if record["exit"] != 0:
            raise Refused(f"{selector}: {record['name']} failed (exit {record['exit']}, "
                          f"timed_out={record['timed_out']}): {record['stderr_tail'][-400:]}")

    destination = generated_root / selector.replace("@", ".")
    receipt["outputs"] = collect(checkout, plan["outputs"], destination)
    receipt["generated_dir"] = str(destination)
    if not receipt["outputs"]:
        # A sandbox that ran to completion and produced nothing is a finding, not a shrug: the
        # 40 DKR files this exists for would go on failing to compile with nothing to explain it.
        raise Refused(f"{selector}: extraction succeeded but produced none of {plan['outputs']}")
    receipt["finished_at"] = int(time.time())
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("selector", help="e.g. dkr@pal.v80")
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--rom", type=Path, default=None)
    ap.add_argument("--keep", action="store_true", help="keep the throwaway sandbox for inspection")
    args = ap.parse_args(argv)
    for tool in (BWRAP, "git", "dpkg-deb"):
        if shutil.which(tool) is None:
            raise SystemExit(f"refusing to run: {tool} is not available")
    receipt = extract(args.selector, rom_override=args.rom, keep=args.keep)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k not in ("steps", "dependencies")},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

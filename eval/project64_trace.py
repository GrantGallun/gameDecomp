"""Bounded Project64 sessions for runtime TRACES: coverage and recording.

The audited entry-capture runner (`eval/project64_runner.py`) takes one paused
snapshot at one function entry, and the live campaign imports it with a pinned
script hash. This is a separate runner for a different evidence kind -- what the
game actually does across calls -- so it leaves that audited path untouched and
reuses only its safety helpers: approved-asset hash checks, refusing to run
beside another emulator, a hidden muted launch, and terminating only its own
process handle.

Traces are EVIDENCE: they come from executing the original ROM, never from
reference C. They are still bounded observations of the paths a session took,
not whole-program equivalence.

Windows Python only (Project64 is a Windows program):

    python eval/project64_trace.py --job JOB.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from eval import project64_runner as audited

KINDS = {"coverage": "project64_trace_coverage.js", "record": "project64_trace_record.js",
         "multi": "project64_trace_multi.js"}
MULTI_LIMITS = (("max_calls", 1, 64), ("max_events", 100, 500_000), ("max_window_ms", 100, 60_000),
                ("max_abandons", 1, 100), ("max_depth", 1, 16))
ROM_SHA256 = "58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c"


def _config(script_name):
    # Audio stays ENABLED: `[Settings] Enable Audio=0` stalled the game (no
    # function was ever reached in 120 s). Sessions are silenced at the OS
    # instead, by `project64_mute.ps1` (see run_job).
    return audited.CONFIG.replace("Autorun Scripts=capture-entry.js", "Autorun Scripts=" + script_name)


def _validate(job):
    if job.get("schema_version") != 1 or job.get("kind") not in KINDS:
        raise ValueError("unsupported trace job")
    duration = job.get("duration_seconds")
    if type(duration) not in {int, float} or not 10 <= duration <= 1800:
        raise ValueError("trace duration must be between 10 and 1800 seconds")
    for key in ("portable_dir", "rom_path", "output_dir"):
        if not isinstance(job.get(key), str) or not Path(job[key]).is_absolute():
            raise ValueError(key + " must be an absolute Windows path")
    if job["kind"] == "record":
        entry, end = job.get("entry"), job.get("end")
        if not (isinstance(entry, int) and isinstance(end, int)
                and 0x80000000 <= entry < end <= 0x80800000 and (end - entry) % 4 == 0):
            raise ValueError("record job needs a word-aligned RAM entry/end")
        if not isinstance(job.get("function"), str):
            raise ValueError("record job needs the function name")
        for key, low, high in (("max_calls", 1, 64), ("max_events", 100, 500_000), ("every", 1, 100_000)):
            if not isinstance(job.get(key), int) or not low <= job[key] <= high:
                raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    if job["kind"] == "multi":
        for key, low, high in MULTI_LIMITS:
            if not isinstance(job.get(key), int) or not low <= job[key] <= high:
                raise ValueError(f"{key} must be an integer in [{low}, {high}]")
        functions = job.get("functions")
        if not isinstance(functions, list) or not 1 <= len(functions) <= 2000:
            raise ValueError("multi job needs 1..2000 functions")
        names, entries = set(), set()
        for fn in functions:
            entry, end = fn.get("entry"), fn.get("end")
            if not (isinstance(fn.get("name"), str) and re.fullmatch(r"[A-Za-z_]\w*", fn["name"])
                    and isinstance(entry, int) and isinstance(end, int)
                    and 0x80000000 <= entry < end <= 0x80800000 and (end - entry) % 4 == 0
                    and isinstance(fn.get("every"), int) and 1 <= fn["every"] <= 10_000_000):
                raise ValueError(f"invalid multi function row: {fn!r}"[:200])
            if fn["name"] in names or entry in entries:
                raise ValueError("duplicate function name or entry in multi job")
            names.add(fn["name"])
            entries.add(entry)


def organize_multi(output: Path) -> dict[str, list[dict]]:
    """Move flat `call-<name>-<n>.json` files into `<name>/call-<n>.json`; checked by content."""
    moved: dict[str, list[dict]] = {}
    for path in sorted(output.glob("call-*-*.json")):
        name, index = re.fullmatch(r"call-([A-Za-z_]\w*)-(\d+)\.json", path.name).groups()
        data = path.read_bytes()
        record = json.loads(data)
        if record.get("function") != name or record.get("kind") != "project64-call-trace":
            raise ValueError(f"{path.name} content does not match its name")
        folder = output / name
        folder.mkdir(exist_ok=True)
        target = folder / f"call-{index}.json"
        path.replace(target)
        moved.setdefault(name, []).append({"path": str(target), "sha256": audited._sha(data)})
    return moved


def _prepare(job, output):
    source = Path(job["portable_dir"]).resolve(strict=True)
    if output.is_relative_to(source):
        raise ValueError("trace output must be outside the source portable bundle")
    assets_data = Path(audited.__file__).with_name("project64_assets.json").read_bytes()
    if audited._sha(assets_data) != audited.ASSETS_SHA256:
        raise ValueError("asset manifest differs from the audited revision")
    assets = json.loads(assets_data)
    portable = output / "portable"
    portable.mkdir()
    for name, expected in assets.items():
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source):
            raise ValueError("portable asset escapes source bundle")
        data = path.read_bytes()
        if audited._sha(data) != expected:
            raise ValueError("portable asset differs from approved revision: " + name)
        target = portable / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    rom_data = Path(job["rom_path"]).resolve(strict=True).read_bytes()
    if audited._sha(rom_data) != ROM_SHA256 or rom_data[:4] != bytes.fromhex("80371240"):
        raise ValueError("trace ROM is not the pinned Snowboard Kids (U) image")
    rom = output / "input.z64"
    rom.write_bytes(rom_data)
    script_name = KINDS[job["kind"]]
    template = Path(__file__).with_name(script_name).read_bytes()
    (portable / "Config" / "Project64.cfg").write_text(_config(script_name), encoding="ascii")
    (portable / "Scripts").mkdir()
    payload = {k: v for k, v in job.items() if k not in ("portable_dir", "rom_path", "output_dir")}
    payload["outputRoot"] = output.as_posix() + "/"
    script = ("var traceJob = " + json.dumps(payload, ensure_ascii=True) + ";\n").encode("ascii") + template
    (portable / "Scripts" / script_name).write_bytes(script)
    audited._write(output / "launch-inputs.json", {
        "kind": job["kind"], "script_sha256": audited._sha(script),
        "exe_sha256": audited.EXE_SHA256, "rom_sha256": ROM_SHA256, "muted_volume": 0})
    return portable / "Project64.exe", rom


def run_job(job):
    output = Path(job["output_dir"])
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    receipt = {"schema_version": 1, "kind": "project64-trace-run", "job_kind": job.get("kind"),
               "status": "error", "output_dir": str(output), "started_unix": time.time(),
               "cleanup": {"owned_pid": None, "terminated": False, "exited": True}}
    process = muter = mute_log = None
    started = time.monotonic()
    try:
        audited._write(output / "job.json", job)
        if not audited._is_windows():
            raise ValueError("trace runner requires Windows Python")
        _validate(job)
        existing = audited._existing_emulators()
        if existing:
            receipt.update(status="blocked_existing_emulator", existing_pids=existing)
            return receipt
        exe, rom = _prepare(job, output)
        process = audited._launch(exe, rom)
        receipt["cleanup"] = {"owned_pid": process.pid, "terminated": False, "exited": False}
        mute_log = (output / "mute.log").open("w", encoding="utf-8")
        muter = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
             str(Path(__file__).with_name("project64_mute.ps1")), "-ProcessId", str(process.pid)],
            stdout=mute_log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = started + job["duration_seconds"]
        result = output / ("coverage.json" if job["kind"] == "coverage" else "trace-status.json")
        while time.monotonic() < deadline and process.poll() is None:
            if job["kind"] in ("record", "multi") and result.exists():
                try:
                    if audited._read(result).get("stage") in ("done", "registration_error"):
                        break                     # every requested call is on disk
                except (ValueError, PermissionError):
                    pass                          # mid-write; read again next tick
            time.sleep(1)
        receipt["emulator_exited_early"] = process.poll() is not None
        if result.exists():
            data = audited._read(result)
            receipt.update(status=data.get("stage", "unknown"), result_path=str(result),
                           result_sha256=audited._sha(result.read_bytes()))
            if job["kind"] == "record":
                calls = sorted(output.glob("call-*.json"))
                receipt["calls"] = [{"path": str(p), "sha256": audited._sha(p.read_bytes())} for p in calls]
            if job["kind"] == "multi":
                receipt["functions"] = data.get("functions", {})
        else:
            receipt["status"] = "no_output"
    except Exception as error:
        receipt.update(status="error", error=f"{type(error).__name__}: {error}")
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    receipt["cleanup"]["terminated"] = True
                process.wait(timeout=10)
                receipt["cleanup"].update(exited=True, exit_code=process.returncode)
            except Exception as error:
                receipt["cleanup"]["error"] = f"{type(error).__name__}: {error}"
                receipt["status"] = "cleanup_error"
        if muter is not None:
            try:
                muter.wait(timeout=15)            # exits on its own once the emulator is gone
            except subprocess.TimeoutExpired:
                muter.terminate()
                muter.wait(timeout=10)
            mute_log.close()
            receipt["muted"] = (output / "mute.log").read_text(encoding="utf-8", errors="replace").splitlines()[:20]
        if job.get("kind") == "multi" and receipt["cleanup"].get("exited"):
            try:                                  # only after the emulator stopped writing
                receipt["calls"] = organize_multi(output)
            except (OSError, ValueError, AttributeError) as error:
                receipt["organize_error"] = f"{type(error).__name__}: {error}"
        receipt["elapsed_seconds"] = time.monotonic() - started
        audited._write(output / "receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    print(json.dumps(run_job(json.loads(Path(args.job).read_text(encoding="utf-8"))), sort_keys=True))


if __name__ == "__main__":
    main()

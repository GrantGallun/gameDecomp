"""Run a deliberately resource-capped Ghidra evidence export.

Ghidra is an optional structural sidecar, never part of the byte-exact oracle.
The first invocation imports and analyzes a stripped ELF once. Later invocations
reuse that cached project with analysis disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GHIDRA = ROOT / ".tools" / "ghidra_12.1.3_PUBLIC"
DEFAULT_INPUT = ROOT / ".tools" / "inputs" / "sbk1-stripped.elf"
DEFAULT_PROJECT_DIR = ROOT / "ghidra-work" / "projects"
DEFAULT_OUTPUT_DIR = ROOT / ".tools" / "ghidra-output"
SCRIPT_DIR = ROOT / "tools" / "ghidra_scripts"

MAX_HEAP_MB = 1024
MAX_CPU = 2
MAX_ANALYSIS_SECONDS = 300
MAX_WALL_SECONDS = 420
MAX_TREE_RSS_MB = 1024
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class Budget:
    heap_mb: int = 512
    cpu: int = 1
    analysis_seconds: int = 120
    wall_seconds: int = 180
    decompile_seconds: int = 30
    tree_rss_mb: int = 768

    def validate(self) -> None:
        if not 256 <= self.heap_mb <= MAX_HEAP_MB:
            raise ValueError(f"heap_mb must be 256..{MAX_HEAP_MB}")
        if not 1 <= self.cpu <= MAX_CPU:
            raise ValueError(f"cpu must be 1..{MAX_CPU}")
        if not 1 <= self.analysis_seconds <= MAX_ANALYSIS_SECONDS:
            raise ValueError(
                f"analysis_seconds must be 1..{MAX_ANALYSIS_SECONDS}")
        if not 1 <= self.wall_seconds <= MAX_WALL_SECONDS:
            raise ValueError(f"wall_seconds must be 1..{MAX_WALL_SECONDS}")
        if not 1 <= self.decompile_seconds <= 120:
            raise ValueError("decompile_seconds must be 1..120")
        if not 384 <= self.tree_rss_mb <= MAX_TREE_RSS_MB:
            raise ValueError(f"tree_rss_mb must be 384..{MAX_TREE_RSS_MB}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_forbidden_names_absent(path: Path, names: list[str]) -> None:
    data = path.read_bytes()
    present = [name for name in names if name.encode("utf-8") in data]
    if present:
        joined = ", ".join(present)
        raise ValueError(f"input contains forbidden reference symbols: {joined}")


def _validated_cached_project(project_dir: Path, project_name: str,
                              output_dir: Path, input_sha256: str) -> bool:
    """Return whether a cache exists, refusing one tied to another binary.

    Ghidra keys a project by the imported filename.  Replacing an ELF with a
    different file of the same name would otherwise silently query stale
    analysis.  Older sidecar runs can be adopted only when their successful
    first-import receipt proves which digest created the project.
    """
    project = project_dir / project_name
    if not (project_dir / f"{project_name}.gpr").exists():
        return False

    marker = project_dir / f"{project_name}.input.sha256"
    if marker.exists():
        recorded = marker.read_text(encoding="ascii").strip().lower()
        if recorded != input_sha256.lower():
            raise RuntimeError(
                f"Ghidra cache {project} was created from input {recorded}, "
                f"not {input_sha256}; use a new project name")
        return True

    for receipt_path in output_dir.glob("*.receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (receipt.get("project") == str(project)
                and receipt.get("input_sha256") == input_sha256
                and receipt.get("used_cached_project") is False
                and receipt.get("return_code") == 0
                and receipt.get("output_exists") is True):
            marker.write_text(input_sha256 + "\n", encoding="ascii")
            return True

    raise RuntimeError(
        f"Ghidra cache {project} has no input-digest marker or successful "
        "first-import receipt; use a new project name")


def _build_headless_args(
        project_dir: Path,
        project_name: str,
        input_elf: Path,
        address: str,
        output_path: Path,
        budget: Budget,
        cached: bool) -> list[str]:
    common = [
        str(project_dir),
        project_name,
        "-max-cpu", str(budget.cpu),
        "-scriptPath", str(SCRIPT_DIR),
    ]
    if cached:
        action = [
            "-process", input_elf.name,
            "-noanalysis",
            "-readOnly",
        ]
    else:
        action = [
            "-import", str(input_elf),
            "-analysisTimeoutPerFile", str(budget.analysis_seconds),
        ]
    return common + action + [
        "-postScript", "ExportFunctionEvidence.java",
        address, str(output_path), str(budget.decompile_seconds),
    ]


def _terminate_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(pid, 15)
        except ProcessLookupError:
            pass


def _tree_rss_bytes(pid: int) -> tuple[int, int]:
    """Return (total RSS, process count) for the launcher and descendants.

    A Java heap setting is not a process-memory cap: metaspace, JIT code and
    mapped files live outside it.  Measuring the complete process tree makes
    the resource receipt honest and lets the wrapper stop pathological runs.
    """
    try:
        import psutil
    except ImportError as exc:  # pragma: no cover - present in the runtime
        raise RuntimeError(
            "psutil is required to enforce the Ghidra process-memory cap") from exc

    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0, 0

    rss = 0
    count = 0
    for process in processes:
        try:
            rss += process.memory_info().rss
            count += 1
        except psutil.Error:
            continue
    return rss, count


def run_export(
        ghidra_dir: Path,
        input_elf: Path,
        project_dir: Path,
        project_name: str,
        output_dir: Path,
        label: str,
        address: str,
        budget: Budget,
        forbidden_names: list[str]) -> dict[str, object]:
    budget.validate()
    if not LABEL_RE.fullmatch(label):
        raise ValueError("label may contain only letters, digits, dot, dash, underscore")
    numeric_address = int(address, 0)
    if not 0 <= numeric_address <= 0xFFFFFFFF:
        raise ValueError("address must fit in 32 bits")
    normalized_address = f"0x{numeric_address:08X}"

    launcher = ghidra_dir / "support" / "analyzeHeadless.bat"
    if os.name != "nt":
        launcher = ghidra_dir / "support" / "analyzeHeadless"
    if not launcher.is_file():
        raise FileNotFoundError(f"Ghidra launcher not found: {launcher}")
    if not input_elf.is_file():
        raise FileNotFoundError(f"stripped ELF not found: {input_elf}")
    _assert_forbidden_names_absent(input_elf, forbidden_names)
    # Fail before launching Java if the process-tree cap cannot be enforced.
    _tree_rss_bytes(os.getpid())

    project_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{label}.json"
    log_path = output_dir / f"{label}.log"
    receipt_path = output_dir / f"{label}.receipt.json"
    input_sha256 = _sha256(input_elf)
    cached = _validated_cached_project(
        project_dir, project_name, output_dir, input_sha256)
    args = _build_headless_args(
        project_dir, project_name, input_elf, normalized_address,
        output_path, budget, cached)

    env = os.environ.copy()
    env["GHIDRA_HEADLESS_MAXMEM"] = f"{budget.heap_mb}M"
    env["GHIDRA_HEADLESS_JAVA_OPTIONS"] = (
        f"-XX:ActiveProcessorCount={budget.cpu} "
        "-XX:ParallelGCThreads=1 -XX:CICompilerCount=2 "
        "-Djava.awt.headless=true"
    )
    if os.name == "nt":
        command = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(
            [str(launcher), *args])]
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        start_new_session = False
    else:
        command = [str(launcher), *args]
        creationflags = 0
        start_new_session = True

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    timed_out = False
    memory_limit_exceeded = False
    peak_tree_rss_bytes = 0
    peak_process_count = 0
    with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        while process.poll() is None:
            rss_bytes, process_count = _tree_rss_bytes(process.pid)
            peak_tree_rss_bytes = max(peak_tree_rss_bytes, rss_bytes)
            peak_process_count = max(peak_process_count, process_count)
            if rss_bytes > budget.tree_rss_mb * 1024 * 1024:
                memory_limit_exceeded = True
                _terminate_tree(process.pid)
                break
            if time.perf_counter() - started > budget.wall_seconds:
                timed_out = True
                _terminate_tree(process.pid)
                break
            time.sleep(0.1)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _terminate_tree(process.pid)
            process.wait(timeout=15)
    wall_seconds = round(time.perf_counter() - started, 3)

    receipt: dict[str, object] = {
        "schema_version": 1,
        "started_at": started_at,
        "label": label,
        "address": normalized_address,
        "input": str(input_elf),
        "input_sha256": input_sha256,
        "input_bytes": input_elf.stat().st_size,
        "ghidra_dir": str(ghidra_dir),
        "project": str(project_dir / project_name),
        "used_cached_project": cached,
        "budget": asdict(budget),
        "timed_out": timed_out,
        "memory_limit_exceeded": memory_limit_exceeded,
        "return_code": process.returncode,
        "wall_seconds": wall_seconds,
        "peak_tree_rss_bytes": peak_tree_rss_bytes,
        "peak_tree_rss_mb": round(peak_tree_rss_bytes / 1024 / 1024, 1),
        "peak_process_count": peak_process_count,
        "output": str(output_path),
        "output_exists": output_path.is_file(),
        "output_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "log": str(log_path),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    if timed_out:
        raise RuntimeError(
            f"Ghidra exceeded the {budget.wall_seconds}s wall limit; see {log_path}")
    if memory_limit_exceeded:
        raise RuntimeError(
            f"Ghidra exceeded the {budget.tree_rss_mb} MiB process-tree limit; "
            f"see {log_path}")
    if process.returncode != 0:
        raise RuntimeError(f"Ghidra exited {process.returncode}; see {log_path}")
    if not output_path.is_file():
        raise RuntimeError(f"Ghidra produced no evidence file; see {log_path}")
    if not cached:
        marker = project_dir / f"{project_name}.input.sha256"
        marker.write_text(input_sha256 + "\n", encoding="ascii")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--ghidra-dir", type=Path, default=DEFAULT_GHIDRA)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--project-dir", type=Path, default=DEFAULT_PROJECT_DIR)
    parser.add_argument("--project-name", default="sbk1_stripped")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--heap-mb", type=int, default=512)
    parser.add_argument("--cpu", type=int, default=1)
    parser.add_argument("--analysis-seconds", type=int, default=120)
    parser.add_argument("--wall-seconds", type=int, default=180)
    parser.add_argument("--decompile-seconds", type=int, default=30)
    parser.add_argument("--tree-rss-mb", type=int, default=768)
    parser.add_argument("--forbid-symbol", action="append", default=[])
    args = parser.parse_args()
    budget = Budget(
        heap_mb=args.heap_mb,
        cpu=args.cpu,
        analysis_seconds=args.analysis_seconds,
        wall_seconds=args.wall_seconds,
        decompile_seconds=args.decompile_seconds,
        tree_rss_mb=args.tree_rss_mb,
    )
    receipt = run_export(
        args.ghidra_dir.resolve(),
        args.input.resolve(),
        args.project_dir.resolve(),
        args.project_name,
        args.output_dir.resolve(),
        args.label,
        args.address,
        budget,
        args.forbid_symbol,
    )
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

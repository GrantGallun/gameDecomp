"""Bounded wavefront experiment with frozen code, binary inputs and history.

This is an unattended *development* experiment unless independently selected
held-out roots are supplied. No test-set membership is bypassed by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

from eval import differential_wavefront as wave
from solver import llm, workspace, transition_policy


class FrozenInputChanged(RuntimeError):
    pass


def file_hashes(paths) -> dict:
    return {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if p.is_file()}


def code_paths(project: Path) -> list[Path]:
    paths = []
    for package in ("solver", "eval", "kb", "patterns", "tools"):
        for directory, dirs, files in os.walk(project / package):
            dirs[:] = [d for d in dirs if d not in {"results", "__pycache__", ".git", ".cache"}]
            paths += [Path(directory) / f for f in files if Path(f).suffix in {".py", ".json", ".yaml", ".toml", ".sh"}]
    return paths


def verify_files(expected: dict) -> None:
    changed = [name for name, digest in expected.items()
               if not Path(name).is_file() or hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
    if changed:
        raise FrozenInputChanged("frozen input changed: " + ", ".join(changed[:5]))


def history_digest(db: Path, functions: tuple[str, ...], attempt_cutoff: int, proposal_cutoff: int) -> str:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in functions)
        rows = list(conn.execute(
            "select a.id,a.source_code,a.compiled,a.score,a.exact,a.diff_summary "
            "from attempts a join functions f on f.addr=a.func_addr where a.id<=? "
            f"and f.name in ({placeholders}) order by a.id", (attempt_cutoff, *functions)))
        proposals = list(conn.execute(
            "select p.id,p.parent_attempt_id,p.raw_response from model_proposals p "
            "join attempts a on a.id=p.parent_attempt_id join functions f on f.addr=a.func_addr "
            f"where p.id<=? and f.name in ({placeholders}) order by p.id", (proposal_cutoff, *functions)))
        return hashlib.sha256(json.dumps([rows, proposals], sort_keys=True).encode()).hexdigest()
    finally:
        conn.close()


def model_digest(endpoint: str, model: str) -> str:
    with urllib.request.urlopen(endpoint.rstrip("/") + "/api/tags", timeout=10) as response:
        tags = json.load(response)
    matches = [m for m in tags.get("models", []) if model in {m.get("name"), m.get("model")}]
    if len(matches) != 1 or not matches[0].get("digest"):
        raise ValueError("cannot freeze immutable local model digest")
    return matches[0]["digest"]


def run(*, project: Path, repo: Path, db: Path, census: Path, output: Path,
        functions: tuple[str, ...], parent_wave: Path | None = None,
        rounds: int = 1, model: str = "gpt-oss:20b", endpoint: str | None = None,
        coverage_cases: int = 5000, stress_cases: int = 256,
        deterministic_only: bool = False, exactness_only: bool = False,
        timeout: int = 1200, num_predict: int = 4000) -> dict:
    if not functions:
        raise ValueError("explicit preselected functions are required")
    if rounds < 0 or coverage_cases < 1 or stress_cases < 1:
        raise ValueError("invalid experiment budget")
    if output.exists() or output.with_name(output.stem + ".wave.json").exists():
        raise ValueError("refusing to overwrite a frozen experiment")
    endpoint = endpoint or llm.host()
    paths = code_paths(project) + [census]
    if parent_wave:
        paths.append(parent_wave)
    paths += [repo / "Makefile", repo / "symbol_addrs.txt", repo / "snowboardkids.yaml"]
    paths += list((repo / "include").rglob("*.h"))
    paths += [repo / "tools/m2ctx.py"]
    paths += list((repo / "asm").rglob("*.s"))
    paths += list((repo / "tools/ido-recomp/linux").glob("*"))
    for function in functions:
        ws = workspace.bootstrap(repo, function)
        paths += list(ws.glob("target*")) + [ws / "build.sh", ws / "prelude.inc"]
    conn = sqlite3.connect(db)
    try:
        attempt_cutoff = conn.execute("select coalesce(max(id),0) from attempts").fetchone()[0]
        proposal_cutoff = conn.execute("select coalesce(max(id),0) from model_proposals").fetchone()[0]
        policy = transition_policy.TransitionPolicy.from_db(conn)
    finally:
        conn.close()
    pinned_files = file_hashes(paths)
    code_inventory = sorted(str(p.resolve()) for p in code_paths(project))
    pinned_history = history_digest(db, functions, attempt_cutoff, proposal_cutoff)
    pinned_model = model_digest(endpoint, model) if rounds and not deterministic_only else None
    receipt = {"kind": "frozen-wavefront-experiment", "schema_version": 1,
               "status": "running", "created_at": int(time.time()),
               "regime": "unattended development replay; not a held-out benchmark",
               "functions": functions, "files": pinned_files, "python": sys.version,
               "attempt_cutoff": attempt_cutoff, "proposal_cutoff": proposal_cutoff,
               "history_sha256": pinned_history, "model_digest": pinned_model,
               "compiler_response_policy_sha256": hashlib.sha256(json.dumps(
                   [t.to_dict() for t in policy.transitions], sort_keys=True).encode()).hexdigest(),
               "interventions": [], "rounds_per_function": rounds,
               "deterministic_only": deterministic_only}
    wave._atomic_json(output, receipt)

    def guard():
        verify_files(pinned_files)
        if code_inventory != sorted(str(p.resolve()) for p in code_paths(project)):
            raise FrozenInputChanged("frozen code file inventory changed")
        if history_digest(db, functions, attempt_cutoff, proposal_cutoff) != pinned_history:
            raise FrozenInputChanged("historical evidence changed during frozen run")

    try:
        guard()
        result = wave.run(
            repo=repo, db=db, sets=project / "eval/sets", census_path=census,
            output=output.with_name(output.stem + ".wave.json"), parent_wave_path=parent_wave,
            model=model, endpoint=endpoint, rounds=rounds, timeout=timeout, think="high",
            num_thread=12, temperature=0.25, diagnosis_num_predict=num_predict,
            patch_num_predict=2000, patch_retries=2, compiler_retries=2,
            max_stalls=2, seed=20260904, cache_dir=None, functions=functions,
            m2c_preflight=False, m2c_stress_cases=stress_cases,
            m2c_coverage_search_cases=coverage_cases, m2c_max_steps=2000,
            frozen_guard=guard, proposal_cutoff_override=proposal_cutoff,
            compiler_response_policy_override=policy, deterministic_only=deterministic_only,
            exactness_only=exactness_only)
        guard()
        if rounds and not deterministic_only and model_digest(endpoint, model) != pinned_model:
            raise FrozenInputChanged("model tag changed during frozen run")
        receipt.update(status="complete", frozen_inputs_unchanged=True,
                       aggregate=result["aggregate"], nodes=result["nodes"])
    except Exception as exc:
        try:
            guard()
            unchanged = not isinstance(exc, FrozenInputChanged)
        except FrozenInputChanged:
            unchanged = False
        receipt.update(status="error" if unchanged else "invalidated", frozen_inputs_unchanged=unchanged,
                       error=f"{type(exc).__name__}: {exc}")
    wave._atomic_json(output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    for flag in ("repo", "db", "census", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--parent-wave", type=Path)
    parser.add_argument("--functions", nargs="+", required=True)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--deterministic-only", action="store_true")
    parser.add_argument("--exactness-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--num-predict", type=int, default=4000)
    parser.add_argument("--coverage-cases", type=int, default=5000)
    parser.add_argument("--stress-cases", type=int, default=256)
    args = parser.parse_args()
    result = run(project=args.project, repo=args.repo, db=args.db, census=args.census,
                 output=args.output, functions=tuple(args.functions), parent_wave=args.parent_wave,
                 rounds=args.rounds, coverage_cases=args.coverage_cases, stress_cases=args.stress_cases,
                 deterministic_only=args.deterministic_only, exactness_only=args.exactness_only,
                 timeout=args.timeout, num_predict=args.num_predict)
    print(json.dumps({"status": result["status"], "aggregate": result.get("aggregate"),
                      "error": result.get("error"), "output": str(args.output)}))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

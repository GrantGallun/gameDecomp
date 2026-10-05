"""Bootstrap a scratch campaign run that makes NO model calls, so the treemap moves without an endpoint.

`eval/resume_pipeline_run.py` is the original bootstrap and the only thing that creates a run
directory. It is unusable now for one reason: line 24 calls `frozen_wavefront.model_digest(endpoint,
model)` unconditionally and the endpoint it was pinned to (`http://172.28.32.1:11435`, `gpt-oss:20b`)
has been gone since 2026-09-17, so every restart of the live run died there and the dashboard sat in
`needs_repair`. The bootstrap also hardcodes its run directory and refuses to reuse one.

The controller it drives, `eval.completion_campaign`, does NOT have that problem: line 605 already
reads `model_digest(...) if model_calls else None`, so a `--model-calls 0` run never touches the
endpoint and records `model_digest: None`. That is the whole path this script uses -- the same
controller, the same immutable ROM-range baseline, the same freeze receipt, with no model.

What is deliberately NOT changed:
  * the baseline hash check, which is the immutability guarantee
  * the `launch.json` manifest, so the run is auditable exactly like the live one
  * `--model-calls 0` is recorded in the state's config, so a later `--resume` with model calls will
    fail `resume configuration differs` rather than silently acquiring a model

    python3 -m eval.scratch_run [--out DIR] [--rounds N] [--max-work-items N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import frozen_wavefront                                          # noqa: E402
from eval import checkpoint_compact                                        # noqa: E402
from solver import llm                                                     # noqa: E402

BASELINE = ROOT / "eval/results/kb-sbk1-rom-ranges-v1.sqlite"
BASELINE_SHA256 = "9f6ce8437a3a5c9657dfa47174c989b2ea9a553bed3cc41b12b12d3a407d8d5a"
SNAPSHOT_DIRS = ("solver", "eval", "kb", "patterns", "tools", "miner", "tests")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/scratch-run-20260917")
    ap.add_argument("--repo", default="/home/grant/decomp/sbk1")
    ap.add_argument("--rounds", type=int, default=3,
                    help="sequential controller invocations; each ends at a checkpoint")
    ap.add_argument("--max-work-items", type=int, default=25)
    ap.add_argument("--model-calls", type=int, default=0,
                    help="0 = deterministic repairs only. >0 uses the endpoint and records its digest")
    ap.add_argument("--endpoint", default=None,
                    help="defaults to solver.llm.host(), which is the WSL gateway on ollama's port")
    ap.add_argument("--model", default="gpt-oss:20b")
    args = ap.parse_args(argv)

    run = args.out.resolve()
    baseline_hash = hashlib.sha256(BASELINE.read_bytes()).hexdigest()
    if baseline_hash != BASELINE_SHA256:
        raise ValueError("immutable ROM-range baseline changed; refusing to bootstrap")
    if run.exists():
        raise ValueError(f"{run} already exists; remove it or choose another --out")

    snapshot = run / "code"
    snapshot.mkdir(parents=True)
    for name in SNAPSHOT_DIRS:
        shutil.copytree(ROOT / name, snapshot / name,
                        ignore=shutil.ignore_patterns("results", "__pycache__", ".cache"))
    shutil.copy2(ROOT / "pytest.ini", snapshot / "pytest.ini")
    database = run / "campaign.sqlite"
    shutil.copy2(BASELINE, database)

    state = run / "campaign.json"
    # `completion_campaign` guards its own freeze check with `if model_calls else None`, so a
    # model-driven run pins the endpoint's digest and a model-free one records None. Resuming with the
    # other setting then fails `resume configuration differs` rather than silently acquiring a model.
    endpoint = args.endpoint or (llm.host() if args.model_calls else None)
    identity = frozen_wavefront.model_digest(endpoint, args.model) if args.model_calls else None
    command = [sys.executable, "-m", "eval.completion_campaign",
               "--repo", args.repo, "--db", str(database), "--project", str(snapshot),
               "--state", str(state), "--scheduler", "evidence-v1",
               "--max-work-items", str(args.max_work_items),
               "--model-calls", str(args.model_calls)]
    if args.model_calls:
        command += ["--model", args.model, "--endpoint", endpoint]
    manifest = {"endpoint": endpoint, "model": args.model if args.model_calls else None,
                "model_digest": identity,
                "baseline_sha256": baseline_hash,
                "command": command, "started_at": time.time(), "deadline_hours": None,
                "regime": ("scratch, model-driven" if args.model_calls else
                           "scratch, model-free: deterministic repairs only, no endpoint"),
                "selection": "entire inventory minus existing frozen held-out sets",
                "integration_requested": False,
                "code_hashes": frozen_wavefront.file_hashes(frozen_wavefront.code_paths(snapshot)),
                "status": "running", "pid": os.getpid()}
    launch = run / "launch.json"
    launch.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"run": str(run), "model_calls": 0, "rounds": args.rounds}), flush=True)

    with (run / "pipeline.log").open("w") as log:
        for round_index in range(args.rounds):
            process = subprocess.Popen(command, cwd=snapshot, stdout=log, stderr=subprocess.STDOUT)
            manifest["worker_pid"] = process.pid
            launch.write_text(json.dumps(manifest, indent=2))
            returncode = process.wait()
            checkpoint = json.loads(state.read_text()) if state.exists() else {}
            # `completion_campaign` writes the FULL state; `progress_map` only reads the compact
            # `campaign-checkpoint-index-v1`. Without this, `/api/map` answers 503 for the whole run
            # and the treemap is useless exactly when it is most wanted -- while the campaign runs.
            # `campaign_state.read` understands both forms, so the next round still resumes.
            try:
                compact = checkpoint_compact.compact(run)
                print(json.dumps({"compacted": compact.get("status"),
                                  "kind": compact.get("kind_after"),
                                  "bytes": [compact.get("bytes_before"),
                                            compact.get("bytes_after")]}), flush=True)
            except Exception as exc:                                      # noqa: BLE001
                print(json.dumps({"compacted": "FAILED",
                                  "error": f"{type(exc).__name__}: {exc}"}), flush=True)
            manifest.update(returncode=returncode, campaign_status=checkpoint.get("status"),
                            summary=checkpoint.get("summary"))
            print(json.dumps({"round": round_index, "returncode": returncode,
                              "checkpoint": checkpoint.get("commit"),
                              "status": checkpoint.get("status"),
                              "summary": checkpoint.get("summary")}), flush=True)
            if returncode != 0:
                manifest["status"] = "worker_exited"
                break
            if "--resume" not in command:
                command = command + ["--resume"]
            manifest["resumptions"] = manifest.get("resumptions", 0) + 1
    manifest["finished_at"] = time.time()
    launch.write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "code_hashes"}, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

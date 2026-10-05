"""Worker entry point for the bounded narrow-RSI experiment, launched by the dashboard.

WHY A SEPARATE FILE FROM `eval/rsi_loop.py`. The loop is the coordinator -- importable, testable, and
resumable by hand. This is the PROCESS the UI starts: it publishes the identity the stop control needs,
honours the operator's stop file, applies the launch-time walls, and exits with a status the supervisor
can read. Keeping them apart means the state machine can be driven in a test or a notebook without
spawning a process, and the dashboard never has to know the coordinator's internals.

THE CONTRACT THE DASHBOARD RELIES ON (declared in `eval/rsi_control.py`):
  argv     --state --stop --pid --run-id --minutes [--config]
  pid file {"run_id": <the --run-id it was given>, "pid": ..., "pgid": ...}
Stop degrades to the STOP file alone when the run id does not match, which is why the pid record has to
carry the id it was launched with rather than one generated here.

WALLS. `--minutes` may only LOWER the configured experiment cap, never raise it: the launch budget is
part of the preregistration, and a UI field that could widen it would let a smoke run quietly become a
different experiment. Config caps are the ceiling; the smaller of the two wins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "eval/results/narrow-rsi-20260921"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--stop", type=Path, default=None)
    ap.add_argument("--pid", type=Path, default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--minutes", type=float, default=None)
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    stop = Path(args.stop) if args.stop else state / "STOP"
    pid_file = Path(args.pid) if args.pid else state / "rsi.pid"
    run_id = args.run_id or f"rsi-{os.getpid()}"

    # Publish identity BEFORE any work so a stop arriving during startup can name this process.
    try:
        pgid = os.getpgid(0)
    except (AttributeError, OSError):
        pgid = os.getpid()
    pid_file.write_text(json.dumps({"run_id": run_id, "pid": os.getpid(), "pgid": pgid}),
                        encoding="utf-8")

    config_path = Path(args.config) if args.config else state / "config.json"
    config = json.loads(config_path.read_text("utf-8")) if config_path.exists() else {}
    if args.minutes:
        capped = min(float(config.get("caps", {}).get("seconds", 1800.0)), args.minutes * 60.0)
        config.setdefault("caps", {})["seconds"] = capped

    from eval.rsi_loop import Experiment

    experiment = Experiment(state, config)
    experiment.stop_path = stop                     # the path the operator's file actually lives at
    experiment.state["run_id"] = run_id
    experiment.publish()
    result = experiment.run()
    print(json.dumps({key: result.get(key) for key in
                      ("experiment_id", "stage", "generation", "candidate", "evidence_status",
                       "gate_reason", "gate_verdict")}, indent=2, default=str))
    return int(result.get("stage") == "failed")


if __name__ == "__main__":
    sys.exit(main())

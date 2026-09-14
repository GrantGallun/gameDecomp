"""Paired repair on one function: identical seed, budget and source; only --recordings differs.

    SOLVER_FIXED_CONTEXT=32768 /home/grant/decomp/sbk1/.venv/bin/python \\
        eval/results/runtime-trace-20260913/repair_pair.py --function initRaceUiBurstTextParticle

Each arm runs in the campaign's own isolated workspace (`campaign_workers.isolate`)
with a private copy of the parked-probe KB, never the live campaign DB. The model is
the campaign's resident gpt-oss:20b on 11435 at its fixed 32k context, so no second
residency is loaded.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval import campaign_workers  # noqa: E402

HOME = Path.home() / "decomp"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", required=True)
    parser.add_argument("--arms", default="recorded,baseline")
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--tag", default="", help="suffix for output directories (never overwrite a prior run)")
    parser.add_argument("--exhaust-budget", action="store_true")
    args = parser.parse_args()
    fn = args.function
    source = HERE / f"replay-{fn}-v3" / "selected.c"
    for arm in args.arms.split(","):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        game = HOME / f"trace-repair-{fn}-{arm}-{stamp}"
        campaign_workers.isolate(HOME / "sbk1", game, fn)
        db = HOME / f"kb-trace-repair-{fn}-{arm}-{stamp}.sqlite"
        shutil.copy2(HOME / "kb-sbk1-parkedprobe-20260913.sqlite", db)
        out = HERE / f"repair-{fn}-{arm}{args.tag}" / "receipt.json"
        if out.parent.exists():
            raise SystemExit(f"{out.parent} exists; choose another --tag")
        command = [sys.executable, "-m", "eval.agentrepair", "--repo", str(game), "--db", str(db),
                   "--function", fn, "--source", str(source), "--out", str(out),
                   "--endpoint", "http://172.28.32.1:11435", "--model", "gpt-oss:20b",
                   "--resilient", "--structured-output", "--retry-invalid", "--include-header-context",
                   "--draws", "1", "--depth", str(args.depth), "--beam", "2", "--max-calls", str(args.max_calls),
                   "--seed", str(args.seed), "--verbose"]
        if args.exhaust_budget:
            command.append("--exhaust-budget")
        if arm == "recorded":
            command += ["--recordings", str(HERE / f"record-{fn}-1")]
        out.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with open(out.parent / "run.log", "w") as log:
            code = subprocess.call(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "SOLVER_FIXED_CONTEXT": "32768"})
        with open(HERE / "repair-pair.status", "a") as status:
            status.write(json.dumps({"arm": arm + args.tag, "exit": code, "argv": sys.argv[1:], "seconds": round(time.time() - started),
                                     "workspace": str(game), "db": str(db)}) + "\n")


if __name__ == "__main__":
    main()

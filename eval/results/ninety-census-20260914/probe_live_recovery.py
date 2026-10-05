"""Run the deployed placeholder_recovery job (live frozen code, zero model calls) on still-blocked nodes.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/probe_live_recovery.py [--workers 2]

Isolated benches and a private KB copy; nothing is written to the campaign. For each function:
whether anything compiled, the best score, and the first IDO error plus clang diagnostics of the
best non-compiling candidate. Writes live-recovery/<fn>.json; reruns skip written functions.
"""
import argparse
import json
import multiprocessing
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parents[0] / "resume-pipeline-20260908"
OUT = HERE / "live-recovery"


def one(function):
    target = OUT / f"{function}.json"
    if target.exists():
        return json.loads(target.read_text())
    sys.path.insert(0, str(RUN / "code"))
    from eval import agentrepair, campaign_state, campaign_workers
    node = campaign_state.read(RUN / "campaign.json")["nodes"][function]
    native = Path(tempfile.mkdtemp(prefix="live-recovery-"))
    entry = {"function": function, "node_score": node.get("score")}
    try:
        repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
        db = native / "kb.sqlite"
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
        receipt = agentrepair.run(repo=repo, db=db, function=function, source=Path(node["source"]).read_text(),
                                  source_parent_attempt_id=None, out=native / "receipt.json",
                                  best_source_out=native / "best.c", model="none", endpoint="http://127.0.0.1:9",
                                  draws=1, depth=4, beam=3, max_calls=0, timeout=10, think="low", num_thread=4,
                                  temperature=0.35, num_predict=100, seed=1, cache_dir=None, verbose=False,
                                  deterministic_budget=0, structured_output=True, retry_invalid=True,
                                  include_header_context=True, resilient=True)
        best = receipt["result"]["best_residual"]
        entry.update(compiled=best.get("compiled"), score=best.get("weighted_progress_score"),
                     frontend=(best.get("frontend") or {}).get("passed"), exact=receipt["result"]["exact"],
                     signature=best.get("compiler_error_signature"),
                     diagnostics=((best.get("frontend") or {}).get("diagnostics") or "")[:6000],
                     best_source=(native / "best.c").read_text())
        with sqlite3.connect(db) as conn:
            rows = conn.execute("SELECT strategy, compiled, score, compiler_stderr FROM attempts WHERE run_id = ?",
                                (receipt["run_id"],)).fetchall()
        entry["attempts"] = len(rows)
        entry["compiled_attempts"] = sum(1 for r in rows if r[1])
        entry["strategies"] = dict(Counter(r[0].split(":")[0] for r in rows).most_common(12))
        entry["first_errors"] = dict(Counter((re.search(r"line \d+: ([^\n]*)", r[3] or "") or [None, "?"])[1][:70]
                                             for r in rows if not r[1]).most_common(6))
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"[:400]
    finally:
        shutil.rmtree(native, ignore_errors=True)
    target.write_text(json.dumps(entry, indent=1))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    blocked = [r["function"] for r in json.loads((HERE / "placeholder-probe.json").read_text()) if r["outcome"] == "still_blocked"]
    with multiprocessing.get_context("spawn").Pool(args.workers) as pool:
        for entry in pool.imap_unordered(one, blocked):
            print(json.dumps({k: entry.get(k) for k in ("function", "compiled", "score", "signature", "first_errors", "error")})[:600],
                  flush=True)


if __name__ == "__main__":
    main()

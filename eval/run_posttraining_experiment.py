"""Run the frozen equal-budget experiment: canary, train M1, evaluate both arms, compare.

READ `eval/equal_budget_eval.py` FIRST. That module owns the preregistration, the panel, the
decision rule and the comparison; this module is the driver that binds a model to each arm and
executes it. Keeping the two apart is deliberate: the measurement instrument must be readable
and testable without loading a 7-billion-parameter model.

WHAT THIS DRIVER GUARANTEES
---------------------------
- THE PANEL IS THE FROZEN ONE. It is read from `PREREGISTRATION.json` and re-hashed; a panel
  that does not match its recorded digest aborts the run rather than measuring a different
  experiment than the one that was preregistered.
- THE ONLY DIFFERENCE BETWEEN ARMS IS THE ADAPTER. Same process, same model object, same
  tokenizer, same sampler settings, same prompt, same oracle, same per-function budget. The
  adapter is enabled for M1 draws and disabled for M0 draws, and the flag is written into every
  receipt so the claim can be audited after the fact.
- DRAW ORDER IS INTERLEAVED PER FUNCTION. If the machine drifts during the run, that drift is
  shared by both arms instead of landing entirely on whichever arm ran second.
- NOTHING IS TUNED ON THE PANEL. There is no code path here that scores a function and then
  decides what to do next. The loop is fixed before the first candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_panel(manifest: dict) -> list[dict]:
    """The frozen panel, verified against the digest recorded when it was preregistered."""
    panel = manifest["panel"]
    digest = hashlib.sha256(json.dumps(panel, sort_keys=True).encode()).hexdigest()
    if digest != manifest.get("panel_sha256"):
        raise SystemExit(
            f"panel digest mismatch: file says {manifest.get('panel_sha256')}, recomputed "
            f"{digest}. The panel is not the one that was preregistered; refusing to run.")
    return panel


def scratch_connection(path: Path, template: Path) -> sqlite3.Connection:
    """A scratch attempt database carrying the real schema and nothing else."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        src = sqlite3.connect(f"file:{template}?mode=ro", uri=True)
        dst = sqlite3.connect(path)
        for _kind, name, sql in src.execute(
                "select type, name, sql from sqlite_master where sql is not null"):
            if name.startswith("sqlite_"):
                continue
            try:
                dst.execute(sql)
            except sqlite3.OperationalError:
                pass
        dst.commit()
        src.close()
    conn = sqlite3.connect(path)
    from kb import attempts as attempt_receipts
    attempt_receipts.ensure_lineage_schema(conn)
    # The receipts reference `functions`; the scratch DB starts empty, so copy the metadata
    # only. No attempt row from the research KB is ever copied: the history is read-only and
    # this experiment's rows must stay separable from it.
    src = sqlite3.connect(f"file:{template}?mode=ro", uri=True)
    for row in src.execute("select addr, name, tu_id, size from functions"):
        conn.execute("insert or ignore into functions (addr, name, tu_id, size) "
                     "values (?,?,?,?)", row)
    for row in src.execute("select id, name from tus"):
        conn.execute("insert or ignore into tus (id, name) values (?,?)", row)
    conn.commit()
    src.close()
    return conn


def provenance_block(manifest: dict, *, kb: Path, dataset: Path | None,
                     extra: dict | None = None) -> dict:
    out = {
        "preregistration_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "panel_sha256": manifest["panel_sha256"],
        "kb": str(kb),
        "dataset": str(dataset) if dataset else None,
        "git_rev": _git_rev(),
        "started_at": int(time.time()),
    }
    if dataset and Path(dataset).exists():
        out["dataset_sha256"] = hashlib.sha256(Path(dataset).read_bytes()).hexdigest()
    if extra:
        out.update(extra)
    return out


def _git_rev() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


# --- the evaluation loop ------------------------------------------------------

def evaluate(manifest: dict, *, model, arms: list[str], out: Path, repo: Path, kb: Path,
             sampler, only: str | None, max_seconds: float, draws: int | None,
             scratch: Path, limit: int = 0) -> dict:
    from eval import trajectory_factory as tf
    from eval import resource_limits as limits_module

    panel = check_panel(manifest)
    if only:
        panel = [row for row in panel if row["function"] == only]
    # A prefix of the frozen order, which is (size, name) sorted. Bounding a run this way is a
    # RESOURCE decision and is reported as one: the panel order is fixed before any result is
    # seen, so a prefix cannot be chosen to favour a result, but it is a smaller panel and the
    # report says how many functions were actually run.
    if limit:
        panel = panel[:limit]
    draws = draws or manifest["design"]["draws_per_function_per_arm"]
    conn = scratch_connection(scratch, kb)
    context_for = tf.make_context(tf.GAMES["sbk1"])

    write_mode = ("repair" if manifest["design"].get("repair_passes")
                  else "independent")
    scorers = {}
    for arm in arms:
        scorers[arm] = tf.WorkspaceScorer(repo, scratch, strategy=f"eval-{arm}",
                                          run_id=f"eval-{arm}-{int(time.time())}")
        scorers[arm].conn = conn

    receipts_path = out / "draws.jsonl"
    out.mkdir(parents=True, exist_ok=True)
    deadline = (time.monotonic() + max_seconds) if max_seconds else None
    results: dict[str, list[dict]] = {arm: [] for arm in arms}
    stopped_for = ""
    started = time.time()

    for entry in panel:
        if deadline is not None and time.monotonic() >= deadline:
            stopped_for = "time budget"
            break
        if limits_module.paused():
            # A human wants the machine back. Checked between functions, so stopping costs at
            # most the function in flight, and every completed draw is already stored.
            stopped_for = "paused"
            print(json.dumps({"paused": limits_module.wait_note()}), flush=True)
            break
        for arm in arms:
            model.set_adapter(arm == "M1")
            generator = __import__("eval.local_model", fromlist=["x"]).InProcessGenerator(
                model, sampler, arm=f"{arm}:{write_mode}")
            try:
                factory = tf.Factory(generator=generator, scorer=scorers[arm],
                                     context_for=context_for,
                                     rounds=manifest["design"]["rounds"],
                                     samples=draws,
                                     temperature=manifest["design"]["sampling"]["temperature"],
                                     # Pure best-of-N. The preregistered design is independent
                                     # draws only, and `repair_from_round` beyond `rounds` makes
                                     # that structural rather than a consequence of rounds==1.
                                     repair_from_round=99,
                                     game="sbk1", compiler="ido-5.3")
                outcome = factory.run_function(
                    {"name": entry["function"], "addr": entry["addr"], "best_score": 0.0,
                     "best_attempt_id": None, "faults": {}, "owned_share": 0.0, "attempts": 0},
                    [draws], deadline=deadline)
                row = {"arm": arm, "function": entry["function"], "tier": entry["tier"],
                       "size": entry["size"], "draws": outcome.attempts,
                       "compiled": outcome.admitted, "exact": outcome.exact,
                       "best_score": outcome.best_after,
                       "errors": outcome.errors, "refusals": outcome.refusals,
                       "stopped": outcome.stopped,
                       "adapter_active": arm == "M1",
                       "model_identity": model.identity()}
            except Exception as exc:
                # A panel member that cannot even be bootstrapped is INELIGIBLE, not a zero for
                # the arm. Measured 2026-09-20: `gspF3DLX_fifoTextStart` is SDK macro assembly
                # (`solver.target_intake.AssemblyBackendRequired`), and a bare exception ended
                # the whole run at function 57 of 60 -- discarding 56 functions of completed
                # work and reporting nothing. The failure is recorded with its reason and the
                # panel continues; the arm summary counts it separately from a compile failure.
                reason = f"{type(exc).__name__}: {exc}"[:400]
                row = {"arm": arm, "function": entry["function"], "tier": entry["tier"],
                       "size": entry["size"], "draws": 0, "compiled": 0, "exact": False,
                       "best_score": None, "errors": 0, "refusals": 0,
                       "stopped": "panel member ineligible", "ineligible_reason": reason,
                       "adapter_active": arm == "M1",
                       "model_identity": model.identity()}
                print(json.dumps({"ineligible": entry["function"], "arm": arm,
                                  "reason": reason}), file=sys.stderr, flush=True)
            if generator.errors:
                # Surfaced, not counted and dropped. An error count with no message is a run
                # that reports "0 exact" and cannot say why -- which is indistinguishable from
                # a model that simply could not do the task.
                row["error_sample"] = generator.errors[:3]
            results[arm].append(row)
            with receipts_path.open("a", encoding="utf-8") as handle:
                for gen_row in generator.rows:
                    handle.write(json.dumps({"function": entry["function"], **gen_row}) + "\n")
            print(json.dumps(row), flush=True)

    summary = {"arms": {}, "seconds": round(time.time() - started, 1),
               "draws_per_function": draws, "stopped_for": stopped_for,
               "panel_functions_run": len(panel), "panel_limit": limit}
    for arm in arms:
        rows = results[arm]
        calls = sum(row["draws"] for row in rows)
        scored = [row for row in rows if row["best_score"] is not None]
        summary["arms"][arm] = {
            "functions_run": len(rows),
            "functions_ineligible": sum(1 for row in rows if row["draws"] == 0),
            "model_calls": calls,
            "functions_exact": sum(1 for row in rows if row["exact"]),
            "compiled": sum(row["compiled"] for row in rows),
            "compile_rate": round(sum(row["compiled"] for row in rows) / calls, 4)
            if calls else None,
            "mean_best_score": round(sum(row["best_score"] for row in scored) / len(scored), 3)
            if scored else None,
            "errors": sum(row["errors"] for row in rows),
            "refusals": sum(row["refusals"] for row in rows),
            "per_function": rows,
        }
    return summary


def compare(summary: dict) -> dict:
    from eval.equal_budget_eval import compare as paired_compare
    m0 = summary["arms"]["M0"]
    m1 = summary["arms"]["M1"]
    out = paired_compare(m0, m1)
    out["cost"] = {
        "seconds": summary["seconds"],
        "model_calls_per_arm": {"M0": m0["model_calls"], "M1": m1["model_calls"]},
        "exact_per_gpu_hour": {
            arm: round(3600.0 * summary["arms"][arm]["functions_exact"]
                       / max(summary["seconds"], 1.0), 4) for arm in ("M0", "M1")},
    }
    return out


# --- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preregistration", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scratch-db", type=Path,
                    default=Path.home() / "decomp" / "posttraining-m1-20260920" / "eval.sqlite")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp" / "kb-sbk1.sqlite")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--model", type=Path,
                    default=Path.home() / "decomp" / "models" / "qwen2.5-coder-7b")
    ap.add_argument("--adapter", type=Path, default=None)
    ap.add_argument("--arms", default="M0,M1")
    ap.add_argument("--draws", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-new-tokens", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--max-model-len", type=int, default=12288)
    ap.add_argument("--only", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N functions of the frozen panel order")
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args(argv)

    from eval.local_model import LocalModel, Sampler

    manifest = json.loads(args.preregistration.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    model = LocalModel(args.model, max_model_len=args.max_model_len)
    if args.adapter:
        info = model.load_adapter(args.adapter)
        print(json.dumps({"adapter_loaded": info}, indent=2), flush=True)
    if "M1" in arms and not model.adapter_path:
        raise SystemExit("arm M1 was requested but no --adapter was given")

    sampler = Sampler(temperature=args.temperature, top_p=args.top_p,
                      max_new_tokens=args.max_new_tokens, seed=args.seed)
    summary = evaluate(manifest, model=model, arms=arms, out=args.out, repo=args.repo,
                       kb=args.kb, sampler=sampler, only=args.only,
                       max_seconds=args.max_seconds, draws=args.draws,
                       scratch=args.scratch_db, limit=args.limit)
    summary["provenance"] = provenance_block(
        manifest, kb=args.kb, dataset=None,
        extra={"model": model.identity(), "sampler": sampler.__dict__,
               "arms": arms, "adapter": str(args.adapter or "")})
    if set(arms) >= {"M0", "M1"}:
        summary["comparison"] = compare(summary)
    (args.out / "evaluation.json").write_text(json.dumps(summary, indent=2) + "\n",
                                              encoding="utf-8")
    headline = {"arms": {a: {k: v for k, v in summary["arms"][a].items() if k != "per_function"}
                         for a in arms}}
    if "comparison" in summary:
        headline["comparison"] = {k: v for k, v in summary["comparison"].items()
                                  if k != "gained_by_neither"}
    print(json.dumps(headline, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

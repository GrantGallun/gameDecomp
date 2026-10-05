"""Run the scripted agent against REAL game functions, and write the transcripts.

WHAT THIS PRODUCES, and none of it needs a model:
  * a bar -- what a fixed-order deterministic policy closes on unsolved game functions;
  * transcripts -- `(observation, action, params, result)` per step, labelled by the oracle, which is
    the training set a policy would be cloned from;
  * an honest per-action report -- how often each action was applicable, changed the source, or was
    skipped for a missing input. An action that is never applicable looks identical to one that does
    nothing, which is the failure this project keeps re-learning.

THE ORACLE IS THE WORKSPACE'S. `exact` here is `solver.workspace.score`'s verdict -- the project's
own object comparison against the target -- and every transcript records that, so no claim is made
about which oracle produced it.

READ-ONLY ON THE RATCHET. This harness never writes to `src/`, never flips a `matched` marker and
never mutates the build. It scores candidates and logs attempts, which is what every other harness
here does.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _attempt_to_verdict(attempt) -> dict:
    """Normalise one `solver.workspace.Attempt` into what the runners expect.

    THE REAL FIELDS, verified against the dataclass: `compiled, score, exact, diff, compiler_stderr,
    raw_output, receipt_id, verification, compiler_recipe, frontend, source_attribution`. The first
    version read `attempt.stderr` and guessed `profile`/`faults` attributes that do not exist, so the
    compiler's own error text never reached the prompt and the fault diagnostics were always empty --
    the model was being asked to act on observations it had not been given.

    No fault profile is invented here. `Attempt` carries no fault classification, and deriving one
    requires the supported path (`solver.signals`); until that is wired the residual is reported as
    the DIFF plus `compiler_stderr`, which is what the oracle actually returned.
    """
    def _get(name, default=None):
        return getattr(attempt, name, default)

    return {
        "compiled": bool(_get("compiled", False)),
        "exact": bool(_get("exact", False)),
        "score": _get("score"),
        "diff": _get("diff", "") or "",
        "stderr": _get("compiler_stderr", "") or "",
        "dump": _get("raw_output", "") or "",
        "receipt_id": _get("receipt_id"),
        "verification": _get("verification"),
        "frontend": _get("frontend"),
        "source_attribution": _get("source_attribution"),
        # The recipe names the TU target (`build/src/.../x.o`), which `header_variant` needs. It was
        # already on the Attempt and never reached the context or the policy.
        "compiler_recipe": _get("compiler_recipe"),
    }


def build_context(repo: Path, function: str, *, conn=None, limit_seconds: float = 120.0):
    """A real Context for one function: m2c's draft, the target assembly, and the oracle."""
    from solver import workspace
    from eval.tool_agent import Context

    ws = workspace.bootstrap(repo, function)
    candidate = workspace.m2c_draft(ws) or ""
    if not candidate.strip():
        return None, f"m2c produced no draft for {function}"

    dump_path = ws / f"{function}_object_dump_normalized.s"

    def compile_fn(source: str, _ws=ws, _fn=function, **log_metadata) -> dict:
        started = time.time()
        attempt = workspace.score(_ws, repo, _fn, source, conn=conn, func=_fn, **log_metadata)
        verdict = _attempt_to_verdict(attempt)
        # THE CANDIDATE'S NORMALIZED OBJECT DUMP, which only exists for a candidate that compiled. It is
        # read here rather than after the fact because it belongs to THIS compile of THIS source: a dump
        # read later describes whichever candidate was compiled last, and `regalloc-search` ranks
        # mutations by comparing exactly this text against the target's.
        if verdict.get("compiled") and dump_path.is_file():
            verdict["dump"] = dump_path.read_text(encoding="utf-8", errors="replace")
        verdict["seconds"] = round(time.time() - started, 2)
        if verdict["seconds"] > limit_seconds:
            verdict["slow"] = True
        return verdict

    # THE DIFF MUST COME FROM THE CANDIDATE UNDER CONSIDERATION. `diffrepair` reads the compiler's
    # own diff, so a diff looked up from the knowledge base describes some earlier candidate and not
    # this one -- and on the first three functions the lookup returned nothing at all, which made
    # `diffrepair` report not-applicable on every task. Compiling once here supplies the real diff
    # and is the same observation the policy needs first anyway.
    #
    # THE LATEST-KB-DIFF FALLBACK IS DELETED, not gated. It could only be sound if the recorded
    # source, target and compiler recipe all matched the ones in hand, and the query cannot establish
    # that -- it selects by function address and takes the newest row. Feeding `diffrepair` a verdict
    # about a different source is worse than feeding it nothing: the action would then edit THIS
    # candidate according to ANOTHER candidate's faults.
    initial = compile_fn(candidate)
    diff = (initial.get("diff") or "").strip()

    # THE OBSERVATIONS THREE ACTIONS NEED, WHICH THE CONTEXT NEVER CARRIED. `Context` has declared
    # `target_dump` and `workspace` since it was written and `build_context` set neither, so on the
    # size-bucketed frame (`eval/results/intake-20260921/class-control.json`):
    #   regalloc-search   declined 6 of 40 with "the context does not carry target_dump"
    #   uopt-trace        declined 40 of 40 -- a separate wiring defect on top of this one
    #   diffrepair        declined 40 of 40, correctly, because a candidate that does not compile has no
    #                     diff to repair from. That one is NOT fixed by populating anything: it is
    #                     unreachable in this phase, and the runner now says so in those words.
    # Both dumps are the oracle's own normalized object text, written by the compile that just ran. They
    # are target-derived (the first) and candidate-derived (the second), never inferred.
    target_dump = ws / "target_object_dump_normalized.s"
    dump = (initial.get("dump") or "").strip() or None

    return Context(function=function, candidate=candidate,
                   target_asm_path=str(ws / "target.s"), source_path=str(ws / "base.c"),
                   target_dump=(target_dump.read_text(encoding="utf-8", errors="replace")
                                if target_dump.is_file() else None),
                   diff=diff or None, compile_fn=compile_fn,
                   repo=str(repo), target=(initial.get("compiler_recipe") or {}).get("target"),
                   workspace=str(ws),
                   # THE CONTROLLER'S FIRST COMPILE IS PART OF THE OBSERVATION. It is handed to the
                   # loop as a recorded step rather than left in `context.diff`, because on the
                   # collected functions this compile FAILS and its stderr is the whole residual --
                   # and a policy shown "unverified" while that verdict sits one field away is being
                   # asked to act on a state it was never given.
                   initial_verdict=initial), ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--functions", default="", help="comma-separated; default: pool head")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--budget", type=int, default=6, help="actions per episode")
    ap.add_argument("--seconds", type=float, default=1800.0, help="stop after this long")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/tool-agent-20260920/transcripts.jsonl")
    args = ap.parse_args(argv)

    from eval.tool_agent import ScriptedPolicy, run_episode
    from eval import tool_runners

    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        # A DIRECT QUERY, and the join is the point: `functions` is a matched-function INVENTORY
        # whose `attempts`/`best_score` columns are entirely NULL (2,113 rows, every one state
        # 'matched'), while the work lives in `attempts` keyed by `func_addr`. Querying `functions`
        # for unsolved work returns nothing and looks exactly like "there is no work to do".
        conn = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
        rows = conn.execute(
            "select f.name, count(a.id) as n, sum(coalesce(a.exact,0)) as solved "
            "from attempts a join functions f on f.addr = a.func_addr "
            "group by f.name having solved = 0 order by max(f.size) asc limit ?",
            (args.limit,)).fetchall()
        conn.close()
        names = [r[0] for r in rows]
        if not names:
            print(json.dumps({"error": "no unsolved functions with attempts in the KB"}))
            return 1

    print(json.dumps({"functions": names, "budget": args.budget, "policy": "scripted"}), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.kb))
    # THE TABLE COMES FROM THE REGISTRY, not from a hand-written subset. The first version of this
    # harness listed four runners explicitly, so `redraft` and `regalloc-search` reported "unwired"
    # on every function while the registry said they were wired -- a capability lost silently, in a
    # report whose whole purpose is to say which actions are available.
    from eval.tool_registry import ACTIONS
    runners = {action.runner: action.resolve()
               for action in ACTIONS.values() if action.runner}
    deadline = time.time() + args.seconds
    summaries = []
    with args.out.open("w", encoding="utf-8") as handle:
        for name in names:
            if time.time() >= deadline:
                summaries.append({"function": name, "skipped": "wall clock"})
                break
            try:
                context, why = build_context(args.repo, name, conn=conn)
            except Exception as exc:                            # noqa: BLE001
                summaries.append({"function": name, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if context is None:
                summaries.append({"function": name, "error": why})
                continue
            transcript = run_episode(context, ScriptedPolicy(), budget=args.budget, runners=runners)
            handle.write(transcript.to_jsonl() + "\n")
            handle.flush()
            summaries.append({
                "function": name, "exact": transcript.exact, "stop": transcript.stop_reason,
                "steps": len(transcript.steps), "candidates_seen": transcript.candidates_seen,
                "seconds": transcript.seconds,
                "actions": [{"action": s.action, "status": s.status, "changed": s.changed,
                             "exact": s.exact} for s in transcript.steps]})
            print(json.dumps(summaries[-1]), flush=True)
    conn.close()

    payload = {"policy": "scripted", "budget": args.budget, "functions": len(names),
               "exact": sum(1 for s in summaries if s.get("exact")),
               "summaries": summaries}
    (args.out.parent / "scripted-run.json").write_text(json.dumps(payload, indent=2) + "\n",
                                                       encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "summaries"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

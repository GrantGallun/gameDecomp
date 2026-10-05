"""THE CONTROL THAT CLOSES THE TRAINING QUESTION.

WHAT IS BEING TESTED. The action adapter moved the chooser and moved certified matches by zero: on
identical states, base 0.600 acceptable / 0 certified, adapter 0.833 / 0, scripted 0.808 / 0. The
missing explanation is whether that zero is a fact about the chooser or a fact about the STATES. If no
action in the original space can improve the certificate from a non-compiling draft, then the action
dataset was composed of states where no action helps -- and that is the reason more training produced a
better procedure and an identical outcome, not a hypothesis about it.

HOW IT DIFFERS FROM `eval.intake_probe`, and why both exist. Same frame, same `build_context`, same
baseline verdict, same recompile-after-change rule, same per-function loop. The ONLY difference is the
action set: this runs the SEVEN WIRED ACTIONS OF THE ORIGINAL SPACE (`eval.tool_registry.ACTIONS` minus
the four intake actions and minus `stop`, which is not a runner) in the scripted order
`eval.tool_agent.SCRIPTED_ORDER` declares. Anything else that differs between the two reports would
make the comparison uninterpretable, so the two modules deliberately share `rank` and the frame format.

WHAT COUNTS AS AN IMPROVEMENT. The certificate's own ordering, not a similarity score:

    exact  >  compiles (score in [0,100])  >  does not compile

and a source that still does not compile is compared by HOW MANY compiler errors it still produces,
because that is the only movement available at that level and it is measured rather than judged. A
function's step is `improved` when its best output ranks strictly above the baseline, and `exact` when
the certificate says so. Firing is recorded separately and is NOT counted as improvement.

NO MODEL IS CALLED. Nothing is promoted. Every compile goes through `solver.workspace.score`, which is
also what logs the attempt -- the same oracle the intake probe uses, so the two are comparable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))

from eval.intake_probe import TIER_ORDER, rank, resolution    # noqa: E402  — shared, deliberately

# THE ORIGINAL SPACE: the seven wired actions the policy could choose before the four intake actions
# were exposed. `compile` is included deliberately -- it cannot move the source (it IS the observation),
# so a control that omitted it could be accused of dropping an action that "would have worked".
ORIGINAL = (
    ("compile", {}),
    ("invert-mutations", {"combinations": True}),
    ("resolve-placeholders", {}),
    ("diffrepair", {}),
    ("redraft", {}),
    ("regalloc-search", {"budget": 64, "beam": 8}),
    ("uopt-trace", {"level": "5"}),
)

_ERROR = re.compile(r"line\s+\d+:\s*(?P<what>.+)$")


def error_classes(stderr: str) -> tuple[int, tuple[str, ...]]:
    """(how many compiler errors, their class multiset) -- the only movement visible below compiling."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    classes = []
    for line in lines:
        if not line.startswith("cfe:"):
            continue
        match = _ERROR.search(line)
        classes.append((match.group("what") if match else line)[:60])
    return len(classes), tuple(sorted(classes))


def verdict_rank(verdict: dict | None, *, errors: int) -> tuple:
    """The control's ordering. Richer than `intake_probe.rank` ONLY below compiling.

    The intake probe compares a still-not-compiling candidate at score 0.0 against a baseline at 0.0 and
    calls them equal, which is correct there: the intake route either opens the front door or it does
    not. Here the whole question is whether the original actions move the certificate AT ALL, so a draft
    that goes from 9 compiler errors to 3 counts as movement and must be visible. It is reported as
    `improved` and never as `converted` or `exact`.
    """
    verdict = verdict or {}
    if verdict.get("compiled"):
        return (2, bool(verdict.get("exact")), float(verdict.get("score") or 0.0), 0)
    return (1, False, 0.0, -int(errors))          # fewer remaining errors ranks higher


def build_frame_context(repo: Path, entry: dict, conn):
    """One frame member: same state construction as the intake probe, plus the drift check and the
    target dump.

    THE STATE MUST BE THE ONE THE FRAME FROZE. `build_context` re-derives the m2c draft from the binary,
    so if the draft has changed since the frame was written the two arms are not measuring the same
    states. That is recorded as `baseline_drift` rather than assumed away.

    WHY `target_dump` IS ADDED HERE. `build_context` does not set it, so `regalloc-search` -- one of the
    most evidenced interventions in this repository -- declines on every task with "the context does not
    carry target_dump" and would be recorded as an action that does not improve the certificate when it
    was never handed its input. That is precisely the silent-decline failure this project keeps paying
    for, and a negative control is worth nothing if it is produced that way. The dump is the NORMALISED
    TARGET OBJECT the oracle already keeps in the function's workspace: target-derived, candidate-
    independent, and the same file `eval/close_nearmiss.py` reads. When the file is absent the key stays
    None and the action declines by name, which is then visible in the per-action report instead of
    being scored as a failed attempt.
    """
    from eval.tool_agent_run import build_context

    context, why = build_context(repo, entry["function"], conn=conn)
    if context is None:
        return None, None, why
    initial = dict(context.initial_verdict or {})
    digest = hashlib.sha256((context.candidate or "").encode("utf-8")).hexdigest()
    drift = bool(entry.get("draft_sha256")) and digest != entry.get("draft_sha256")
    dump = repo / "nonmatchings" / entry["function"] / "target_object_dump_normalized.s"
    if dump.is_file():
        context.target_dump = dump.read_text(encoding="utf-8", errors="replace")
    return context, initial, ("draft changed since the frame was frozen" if drift else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--frame", type=Path, required=True,
                    help="the frozen frame payload the intake arm was measured on")
    ap.add_argument("--split", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--want", type=int, default=0, help="cap the frame (0 = the whole frame)")
    ap.add_argument("--seconds", type=float, default=3600.0)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/intake-20260921/class-control.json")
    args = ap.parse_args(argv)

    from eval.tool_registry import ACTIONS

    held_out = set()
    if args.split.exists():
        held_out = set(json.loads(args.split.read_text(encoding="utf-8")).get("test") or [])

    previous = json.loads(Path(args.frame).read_text(encoding="utf-8"))
    frame = [r for r in previous["rows"] if r["function"] not in held_out]
    if args.want:
        frame = frame[:args.want]
    if not frame:
        print(json.dumps({"error": f"the frame {args.frame} has no usable rows"}))
        return 1

    # THE ACTION SET IS RESOLVED FROM THE REGISTRY, not hand-listed, and the four intake actions are
    # named EXPLICITLY so the exclusion is visible in the report rather than implied by a shorter tuple.
    # Two earlier versions of this check were wrong, in ways worth recording because each looked correct:
    #   1. `endswith("intake_runners")` against the runner path -- `eval.intake_runners.globals_variant`
    #      does not end with that string, so the report printed an EMPTY exclusion list while excluding
    #      them. An empty list there reads as "no action was withheld".
    #   2. asserting the runner path ends with the action's own NAME -- but an action's key and its
    #      runner's function are different identifiers (`header-context` -> `header_variant`,
    #      `globals-declare` -> `globals_variant`), so the check fired on a registry that was fine.
    # What actually matters is only that the control does not run an intake action, so that is what is
    # asserted, plus the fact that each named action still resolves to the intake module -- not to a
    # specific function name inside it.
    from eval import intake_runners

    intake_names = ("header-context", "globals-declare", "opaque-struct", "do-while-rewrite")
    runners = {name: ACTIONS[name].resolve() for name, _ in ORIGINAL}
    assert not (set(runners) & set(intake_names)), "the control must not run an intake action"
    for name in intake_names:
        assert name in ACTIONS, f"the registry no longer declares {name}"
        module = ACTIONS[name].runner.rpartition(".")[0]
        assert module == intake_runners.__name__, (
            f"{name} is expected to be an intake action, but its runner lives in {module!r}")

    conn = sqlite3.connect(str(args.kb))      # read-write: workspace.score logs the attempt
    started, errors, rows = time.time(), [], []
    per_action: dict[str, dict] = {}
    try:
        for entry in frame:
            if time.time() - started > args.seconds:
                errors.append({"function": entry["function"], "stage": "loop",
                               "error": "wall clock reached before this function was measured"})
                break
            name = entry["function"]
            try:
                context, initial, drift = build_frame_context(args.repo, entry, conn)
            except Exception as exc:                              # noqa: BLE001
                errors.append({"function": name, "stage": "build_context",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if context is None:
                errors.append({"function": name, "stage": "build_context", "error": drift})
                continue
            if initial.get("compiled"):
                # A frame member that compiles now is NOT a front-door failure any more, and the intake
                # arm measured it as such. Recorded, not silently dropped, so both arms count it.
                rows.append({"function": name, "tier": entry.get("tier"), "size": entry.get("size"),
                             "baseline_compiled": True, "drift": bool(drift),
                             "actions": {}, "improved": False, "exact": False,
                             "best": {"compiled": True, "exact": bool(initial.get("exact")),
                                      "score": initial.get("score")},
                             "errors_remaining": 0, "baseline_errors": 0})
                continue

            baseline_errors, baseline_classes = error_classes(initial.get("stderr") or "")
            base = verdict_rank(initial, errors=baseline_errors)
            row = {"function": name, "tier": entry.get("tier"), "size": entry.get("size"),
                   "failure_class": entry.get("failure_class"),
                   "baseline_compiled": False, "drift": bool(drift),
                   "baseline_score": initial.get("score"),
                   "baseline_errors": baseline_errors,
                   "baseline_error_classes": list(baseline_classes[:4]),
                   "baseline_stderr": (initial.get("stderr") or "")[:160],
                   "actions": {}, "best": None}
            best, best_label = base, None
            counters = {kind: 0.0 for kind, _ in ORIGINAL}

            for label, params in ORIGINAL:
                runner = runners[label]
                namespace = {**context.__dict__, "kb_conn": conn}
                try:
                    result = runner(namespace, dict(params))
                except Exception as exc:                          # noqa: BLE001
                    errors.append({"function": name, "stage": label,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    row["actions"][label] = {"error": f"{type(exc).__name__}: {exc}"}
                    continue

                produced: list[tuple[str, str]] = []
                if result.get("changed") and isinstance(result.get("source"), str):
                    produced.append(("<source>", result["source"]))
                for candidate in (result.get("candidates") or []):
                    if isinstance(candidate, dict) and isinstance(candidate.get("source"), str):
                        produced.append((str(candidate.get("name")), candidate["source"]))
                if label == "compile":
                    # The observation action: it re-states the baseline verdict. Kept in the record so
                    # the "it is not a transform" claim is a measurement, not a reading of the code.
                    produced = []

                item = {"changed": bool(result.get("changed")), "status": result.get("status"),
                        "produced": len(produced), "reason": str(result.get("reason") or "")[:160]}
                if label == "regalloc-search":
                    item["search"] = {k: result.get(k) for k in
                                      ("compiles", "trace_calls", "best_label", "certified")}
                verdicts = []
                for candidate_label, source in produced:
                    verdict = context.compile_fn(source)
                    remaining, classes = error_classes(verdict.get("stderr") or "")
                    verdicts.append({"name": candidate_label[:60],
                                     "compiled": bool(verdict.get("compiled")),
                                     "exact": bool(verdict.get("exact")),
                                     "score": verdict.get("score"),
                                     "errors_remaining": 0 if verdict.get("compiled") else remaining,
                                     "error_classes": list(classes[:2])})
                    this = verdict_rank(verdict, errors=remaining)
                    if this > best:
                        best, best_label = this, f"{label}:{candidate_label}"[:80]
                if verdicts:
                    item["verdicts"] = verdicts[:6]
                    item["any_compiled"] = any(v["compiled"] for v in verdicts)
                    item["any_exact"] = any(v["exact"] for v in verdicts)
                    item["best_score"] = max((v["score"] or 0.0) for v in verdicts)
                row["actions"][label] = item
                stat = per_action.setdefault(label, {"fired": 0, "source_changed": 0, "compiled": 0,
                                                     "exact": 0, "declined": 0, "not_applicable": 0,
                                                     "candidates": 0, "seconds": 0.0})
                stat["fired"] += int(bool(produced))
                stat["source_changed"] += int(bool(result.get("changed")))
                stat["compiled"] += int(item.get("any_compiled", False))
                stat["exact"] += int(item.get("any_exact", False))
                stat["declined"] += int(result.get("status") == "no-change")
                stat["not_applicable"] += int(result.get("status") == "not-applicable")
                stat["candidates"] += len(produced)

            row["improved"] = best > base
            row["compiling_anywhere"] = best[0] >= 2
            row["exact"] = best[0] == 2 and best[1]
            row["best_label"] = best_label
            row["errors_remaining_at_best"] = -best[3] if best[0] == 1 else 0
            rows.append(row)
            print(json.dumps({"function": name, "tier": row["tier"],
                              "baseline_errors": baseline_errors,
                              "improved": row["improved"], "compiling": row["compiling_anywhere"],
                              "exact": row["exact"],
                              "fired": [k for k, v in row["actions"].items() if v.get("changed")]}),
                  flush=True)
    finally:
        conn.close()

    live = [r for r in rows if not r.get("baseline_compiled")]
    by_tier = {name: {"n": 0, "improved": 0, "compiling": 0, "exact": 0} for name in TIER_ORDER}
    for row in live:
        stat = by_tier.setdefault(row.get("tier") or "unknown",
                                  {"n": 0, "improved": 0, "compiling": 0, "exact": 0})
        stat["n"] += 1
        stat["improved"] += int(bool(row["improved"]))
        stat["compiling"] += int(bool(row["compiling_anywhere"]))
        stat["exact"] += int(bool(row["exact"]))
    moved = [r["function"] for r in live if r["actions"] and
             any(v.get("changed") for v in r["actions"].values())]
    payload = {
        "schema_version": 1,
        "frame": str(args.frame), "frame_size": len(rows), "measured": len(live),
        "frame_by_tier": {name: sum(1 for r in rows if r.get("tier") == name) for name in TIER_ORDER},
        "actions": [name for name, _ in ORIGINAL],
        "intake_actions_excluded": intake_names,
        "errors": errors,
        "baseline_drift": [r["function"] for r in rows if r.get("drift")],
        "per_action": per_action, "by_tier": by_tier, "rows": rows,
        # THE HEADLINE THE DECISION RULE NEEDS. `improved` counts a certificate that moved at all;
        # `certified_matches` counts what the rest of this project counts.
        "any_action_improved_any_state": sum(1 for r in live if r["improved"]),
        "improved_states": [r["function"] for r in live if r["improved"]],
        "certified_matches": sum(1 for r in live if r["exact"]),
        "states_where_source_moved": len(moved),
        # THE FRAME'S OWN NOISE FLOOR. An improvement count means nothing without the smallest effect the
        # frame can distinguish from membership drift, and the control arm is the one that reports
        # COUNTS of improved states -- the number most likely to be read as a signal when it is one state.
        "resolution": resolution(len(live)),
        "planned_frame": len(frame), "dropped_from_frame": len(frame) - len(rows),
        "seconds": round(time.time() - started, 1),
        "harness_clean": not errors,
        "note": ("the original seven wired actions, run one at a time from the frozen frame's non-"
                 "compiling drafts, judged by the certificate. No model was called. Firing is recorded "
                 "separately and is not counted as improvement."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in
                      ("frame_size", "measured", "any_action_improved_any_state", "certified_matches",
                       "states_where_source_moved", "errors", "baseline_drift", "by_tier", "seconds")},
                     indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())

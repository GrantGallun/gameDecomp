"""The promotion rule, declared before the evaluation and enforced after it.

THE RULE
--------
An adapter is promoted only when ALL of the following hold on the frozen held-out panel:

  R0  the evaluation is ELIGIBLE: it is the frozen `test` split, it is not a diagnostic/subset
      run, and it declares the panel and the per-arm draw budget it was supposed to execute;
  R1  it closes at least one object-exact task that the baseline did not close;
  R2  it loses none;
  R3  the panel is at least `MIN_TASKS_FOR_A_VERDICT` tasks;
  R4  both arms ran every task of the FROZEN PANEL (not merely the same set as each other);
  R5  each arm actually EXECUTED the declared number of draws per task.

R0, R4 and R5 exist because the first version of this gate compared only the two arms' id sets.
An audit broke it with a direct probe -- 12 matching ids, **0 baseline draws**, 2 adapter draws and
one apparent gain -- and it returned `promote`. Two arms that agree with each other about a panel
neither of them covers are not evidence of anything: they are two incomplete runs that happen to
have the same shape. Coverage is always against the DECLARED panel, and the budget is always the
ACTUAL number of draws, so a run that stopped early cannot satisfy the completeness check by
recording fewer rows.

That is deliberately conservative. A model that trades one exact match for another has not
improved, and with 27 held-out tasks a one-task gain is already at the edge of what the panel
can resolve, so allowing losses would let noise masquerade as progress.

WHAT CANNOT AUTHORISE PROMOTION
-------------------------------
- A training-loss decrease. The receipt records it; the gate never reads it.
- Success on training or dev tasks. Those splits are rejected outright, by name.
- A higher mean similarity score. `score` is diagnostic; `exact` is the verdict, and it is
  the object/relocation certificate produced by the compiler.
- A smoke run, a diagnostic run, or any subset of the panel (`EvaluationSpec.kind`).
- An incomplete panel or a short draw budget (R4, R5).
- A caller that hands over two arms and no frozen specification at all: with nothing declared
  there is no panel to cover and no budget to execute, so `decide` returns `ineligible`.

A FAILED GATE KEEPS THE BASELINE. `decide` returns the baseline as the active adapter on every
non-passing outcome, including `inconclusive` and `ineligible`, and says which condition failed.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2

# The smallest panel the gate will pass on. Below this, a result is INCONCLUSIVE whatever it
# says: with 5 tasks a 1-task gain is a coin flip, and reporting it as capability would be the
# exact overclaim this project keeps catching in itself.
MIN_TASKS_FOR_A_VERDICT = 12

# Only the frozen held-out split can authorise a promotion, and `train`/`dev` are named because a
# caller that accidentally evaluates on training data is the failure this guard exists for.
FROZEN_SPLIT = "test"
FORBIDDEN_SPLITS = frozenset({"train", "dev"})

# Which declared evaluation kinds are promotable. Everything else (`subset`, `diagnostic`,
# `smoke`, ...) is explicitly ineligible rather than merely under-sized.
PROMOTABLE_KINDS = frozenset({"frozen"})


@dataclass(frozen=True)
class EvaluationSpec:
    """What the frozen evaluation declared BEFORE the run: panel, split, per-arm draw budget.

    The gate cannot infer any of this from the results -- that is the whole point. A result file
    that says "12 tasks, both arms agree" is exactly what an incomplete run looks like, so the
    panel and the budget have to come from the freeze, not from the run being judged.
    """

    expected_task_ids: tuple[str, ...] = ()
    split: str = FROZEN_SPLIT
    draws_per_task: int = 0
    kind: str = "frozen"
    manifest_sha256: str = ""
    dataset_sha256: str = ""

    @property
    def expected_draws(self) -> int:
        """Draws per arm implied by the declared panel and budget."""
        try:
            return int(self.draws_per_task) * len(self.expected_task_ids)
        except (TypeError, ValueError):
            return 0

    def ineligible_reasons(self) -> list[str]:
        """Every reason this specification cannot authorise a promotion, in plain words."""
        reasons: list[str] = []
        if self.split != FROZEN_SPLIT:
            detail = ("; train/dev results can never authorise a promotion"
                      if self.split in FORBIDDEN_SPLITS else "")
            reasons.append(f"it is not the frozen {FROZEN_SPLIT!r} split (split={self.split!r}"
                           f"{detail})")
        if self.kind not in PROMOTABLE_KINDS:
            reasons.append(
                f"it is declared {self.kind!r}, and only "
                f"{'/'.join(sorted(PROMOTABLE_KINDS))} evaluations can authorise a promotion; a "
                f"diagnostic or subset run is reported, never promoted")
        if not self.expected_task_ids:
            reasons.append("it declares no frozen panel, so there is no panel to cover and an "
                           "incomplete run would look complete")
        if int(self.draws_per_task or 0) < 1:
            reasons.append("it declares no per-arm draw budget, so a run that executed no draws "
                           "cannot be distinguished from one that executed the budget")
        return reasons

    def as_dict(self) -> dict:
        return {
            "split": self.split,
            "kind": self.kind,
            "panel_size": len(self.expected_task_ids),
            "expected_task_ids": list(self.expected_task_ids),
            "draws_per_task": self.draws_per_task,
            "expected_draws_per_arm": self.expected_draws,
            "manifest_sha256": self.manifest_sha256,
            "dataset_sha256": self.dataset_sha256,
            "eligible": not self.ineligible_reasons(),
            "ineligible_reasons": self.ineligible_reasons(),
        }


@dataclass
class GateOutcome:
    passed: bool
    verdict: str                      # "promote" | "keep-baseline" | "inconclusive" | "ineligible"
    active_adapter: str               # "baseline" | "adapter"
    reasons: list[str] = field(default_factory=list)
    conditions: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["schema_version"] = SCHEMA_VERSION
        out["decided_at"] = int(time.time())
        out["rule"] = {
            "R0": "the evaluation must be an eligible frozen-test-split run with a declared "
                  "panel and draw budget",
            "R1": "the adapter closes at least one object-exact task the baseline did not",
            "R2": "the adapter loses no object-exact task the baseline closed",
            "R3": f"the frozen panel has at least {MIN_TASKS_FOR_A_VERDICT} tasks",
            "R4": "both arms ran every task of the declared frozen panel",
            "R5": "each arm actually executed the declared number of draws per task",
            "cannot_authorise": ["a training-loss decrease",
                                 "success on train or dev tasks",
                                 "a higher mean similarity score",
                                 "a smoke run",
                                 "a diagnostic, subset or otherwise ineligible evaluation",
                                 "an incomplete panel or a short draw budget",
                                 "an undeclared panel or budget"],
        }
        return out


def _draws(row: dict) -> int | None:
    """The draw slots a row records, or None when it records none.

    `bool` is excluded deliberately: `True` is an `int` in Python, and a row that recorded a
    boolean where a count belongs is a malformed row, not one draw.
    """
    value = (row or {}).get("draws")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _generated(row: dict) -> int | None:
    """The draws that actually produced a model response, when the row records them.

    A slot that raised out-of-memory or produced nothing is a slot, not a draw: a run whose
    sampler never ran has no result to compare, however many rows it wrote.
    """
    value = (row or {}).get("draws_generated")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def decide(*, baseline: dict, adapter: dict, spec: "EvaluationSpec | None" = None,
           min_tasks: int = MIN_TASKS_FOR_A_VERDICT) -> GateOutcome:
    """Apply the declared rule to two arms' per-task results and the frozen specification.

    `baseline` and `adapter` map `task_id` to a row with at least `{"exact": bool, "draws": int}`.
    Tasks present in only one arm are reported, not silently dropped: a task the adapter never
    ran is not a task it won. `spec` is the frozen evaluation this run was supposed to execute;
    without it there is nothing to check coverage against and the outcome is `ineligible`.
    """
    spec = spec if isinstance(spec, EvaluationSpec) else EvaluationSpec()
    baseline_rows = {k: v for k, v in (baseline or {}).items()}
    adapter_rows = {k: v for k, v in (adapter or {}).items()}
    panel = list(spec.expected_task_ids)
    panel_set = set(panel)
    shared = sorted(set(baseline_rows) & set(adapter_rows))
    only_baseline = sorted(set(baseline_rows) - set(adapter_rows))
    only_adapter = sorted(set(adapter_rows) - set(baseline_rows))

    def exact(rows, key):
        return bool((rows.get(key) or {}).get("exact"))

    gained = [k for k in shared if exact(adapter_rows, k) and not exact(baseline_rows, k)]
    lost = [k for k in shared if exact(baseline_rows, k) and not exact(adapter_rows, k)]
    both = [k for k in shared if exact(baseline_rows, k) and exact(adapter_rows, k)]
    neither = [k for k in shared if not exact(baseline_rows, k) and not exact(adapter_rows, k)]

    # --- R0: is this evaluation allowed to decide anything at all? --------------------------
    ineligible = spec.ineligible_reasons()

    # --- R4: coverage of the DECLARED panel, which is not the same as arm-vs-arm agreement ---
    missing_baseline = [t for t in panel if t not in baseline_rows]
    missing_adapter = [t for t in panel if t not in adapter_rows]
    extra_baseline = sorted(set(baseline_rows) - panel_set)
    extra_adapter = sorted(set(adapter_rows) - panel_set)
    covered = not (missing_baseline or missing_adapter or extra_baseline or extra_adapter)
    panel_size = len(panel) if panel else len(shared)

    # --- R5: the ACTUAL draws, against the declared budget -----------------------------------
    def unexecuted(rows: dict, label: str) -> list[str]:
        problems = []
        for task_id in panel:
            row = rows.get(task_id)
            if row is None:
                continue
            actual = _draws(row)
            if actual is None:
                problems.append(f"{label} {task_id}: the row records no draw count")
            elif actual != spec.draws_per_task:
                problems.append(
                    f"{label} {task_id}: {actual} draw slot(s), declared {spec.draws_per_task}")
            produced = _generated(row)
            if produced is not None and produced != spec.draws_per_task:
                problems.append(
                    f"{label} {task_id}: only {produced} of {spec.draws_per_task} draw(s) "
                    f"produced a model response")
        return problems

    def executed(rows: dict) -> int:
        total = 0
        for row in rows.values():
            count = _generated(row)
            if count is None:
                count = _draws(row)
            total += count or 0
        return total

    draw_problems = unexecuted(baseline_rows, "baseline") + unexecuted(adapter_rows, "adapter")
    baseline_draws, adapter_draws = executed(baseline_rows), executed(adapter_rows)
    expected_draws = spec.expected_draws
    budget_executed = (not draw_problems and baseline_draws == expected_draws
                       and adapter_draws == expected_draws)

    counts = {
        "tasks_compared": len(shared),
        "panel_tasks": panel_size,
        "baseline_exact": sum(1 for k in shared if exact(baseline_rows, k)),
        "adapter_exact": sum(1 for k in shared if exact(adapter_rows, k)),
        "gained_by_adapter": gained,
        "lost_by_adapter": lost,
        "closed_by_both": both,
        "closed_by_neither": len(neither),
        "only_baseline_ran": only_baseline,
        "only_adapter_ran": only_adapter,
        "panel_never_ran": {"baseline": missing_baseline, "adapter": missing_adapter},
        "tasks_outside_the_panel": {"baseline": extra_baseline, "adapter": extra_adapter},
        "draws": {"expected_per_arm": expected_draws, "baseline": baseline_draws,
                  "adapter": adapter_draws, "per_task_problems": draw_problems[:10],
                  "per_task_problems_total": len(draw_problems)},
        "spec": spec.as_dict(),
    }
    conditions = {
        "R0_spec_eligible": not ineligible,
        "R1_gained_at_least_one": len(gained) >= 1,
        "R2_lost_none": not lost,
        "R3_panel_large_enough": len(shared) >= min_tasks and panel_size >= min_tasks,
        # R4 is COVERAGE OF THE DECLARED PANEL, and it subsumes "both arms ran the same tasks":
        # when both arms equal the panel they equal each other, and when the panel is absent the
        # specification is ineligible before this is read.
        "R4_panel_fully_covered": covered and not only_baseline and not only_adapter,
        "R5_budget_executed": budget_executed,
    }

    reasons: list[str] = []
    if ineligible:
        reasons.append("the evaluation cannot authorise a promotion: " + "; ".join(ineligible))
    if not conditions["R3_panel_large_enough"]:
        reasons.append(
            f"only {len(shared)} paired tasks of a {panel_size}-task panel; {min_tasks} are "
            f"needed before a result means anything. This is a wiring validation, not a "
            f"capability verdict.")
    if only_baseline or only_adapter:
        reasons.append(
            f"the arms did not run the same tasks ({len(only_baseline)} baseline-only, "
            f"{len(only_adapter)} adapter-only); a task only one arm ran cannot be compared")
    if not covered:
        reasons.append(
            f"the run did not execute the frozen panel: "
            f"{len(missing_baseline)} task(s) the panel declares were never run by the baseline, "
            f"{len(missing_adapter)} by the adapter, and "
            f"{len(extra_baseline) + len(extra_adapter)} task(s) outside the panel were run. "
            f"Equal id sets between the arms are not coverage of the declared panel.")
    if not budget_executed:
        reasons.append(
            f"the declared budget was not executed: {baseline_draws} baseline draw(s) and "
            f"{adapter_draws} adapter draw(s) against {expected_draws} per arm "
            f"({spec.draws_per_task} per task over {len(panel)} task(s)); "
            f"{len(draw_problems)} per-task shortfall(s)"
            + (f", first: {draw_problems[0]}" if draw_problems else ""))
    if lost:
        reasons.append(f"the adapter lost {len(lost)} task(s) the baseline closed: {lost[:5]}")
    if not gained:
        reasons.append("the adapter closed no task the baseline did not")

    if not conditions["R0_spec_eligible"]:
        return GateOutcome(False, "ineligible", "baseline", reasons, conditions, counts)
    if not (conditions["R3_panel_large_enough"] and conditions["R4_panel_fully_covered"]
            and conditions["R5_budget_executed"]):
        return GateOutcome(False, "inconclusive", "baseline", reasons, conditions, counts)
    if not gained:
        return GateOutcome(False, "keep-baseline", "baseline", reasons, conditions, counts)
    if lost:
        return GateOutcome(False, "keep-baseline", "baseline", reasons, conditions, counts)
    reasons.append(f"gained {len(gained)} task(s) and lost none: {gained[:5]}")
    return GateOutcome(True, "promote", "adapter", reasons, conditions, counts)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", type=Path, required=True,
                    help="JSON mapping task_id -> {exact: bool, draws: int, ...}")
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--spec", type=Path, default=None,
                    help="JSON object with the frozen specification: expected_task_ids, split, "
                         "draws_per_task, kind, manifest_sha256, dataset_sha256. Without it the "
                         "outcome is `ineligible`: nothing was declared, so nothing can be "
                         "checked, and this CLI exists to APPLY a rule that was declared "
                         "elsewhere.")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--min-tasks", type=int, default=MIN_TASKS_FOR_A_VERDICT)
    args = ap.parse_args(argv)
    payload_spec = json.loads(args.spec.read_text()) if args.spec else {}
    payload_spec["expected_task_ids"] = tuple(payload_spec.get("expected_task_ids") or ())
    outcome = decide(baseline=json.loads(args.baseline.read_text()),
                     adapter=json.loads(args.adapter.read_text()),
                     spec=EvaluationSpec(**payload_spec),
                     min_tasks=args.min_tasks)
    payload = outcome.as_dict()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

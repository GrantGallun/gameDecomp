"""Cross-function compiler-response learning from attempt-DAG transitions.

Rewrite generators are actions, not learned knowledge by themselves.  This
module turns parent -> child compiler receipts into an empirical response model:
given a residual/source context and an action family, what usually compiles,
changes the residual, reduces its fault vector, or reaches exactness?

The policy is deliberately small and inspectable.  It performs hierarchical
backoff over exact context, residual class, dominant fault, then global family
statistics.  When ranking work for a function, all transitions from that
function are excluded.  The compiler and semantic replay remain hard gates;
these estimates only order experiments that would otherwise all be tried.

No finished/reference C enters the model.  Its dataset is reconstructed solely
from candidate attempts, explicit attempt_edges, and compiler-oracle diffs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import defaultdict
import json
import re
import sqlite3
from typing import Iterable, Mapping, Any

from solver import c89, exactness_gradient, signals


MODEL_VERSION = 2


def residual_content(diff: str | None) -> tuple[str, ...]:
    """Ignore unified-diff file headers/hunk locations, never instruction text."""
    lines = str(diff or "").splitlines()
    if len(lines) >= 2 and lines[0].startswith("--- ") and lines[1].startswith("+++ "):
        lines = lines[2:]
    return tuple(line for line in lines if not line.startswith("@@ "))


FAULT_AXES = (
    "structural", "layout", "offset", "width", "relocation",
    "register_allocation", "ordering", "immediate",
)


@dataclass(frozen=True)
class ResidualState:
    classification: str
    faults: dict[str, int]
    diff_lines: int
    instruction_delta: int
    cycle_count: int
    largest_cycle: int
    source_lines: int
    local_count: int
    pointer_local_count: int
    loop_count: int
    branch_count: int
    multi_epoch_local_count: int

    @property
    def dominant_fault(self) -> str:
        nonzero = [(int(self.faults.get(axis, 0)), axis)
                   for axis in FAULT_AXES if self.faults.get(axis, 0)]
        return max(nonzero, default=(0, "none"))[1]

    @property
    def size_band(self) -> str:
        if self.diff_lines <= 8:
            return "small"
        if self.diff_lines <= 32:
            return "medium"
        return "large"

    @property
    def cycle_band(self) -> str:
        if not self.cycle_count:
            return "none"
        return "two" if self.largest_cycle == 2 else "three-plus"

    def context_keys(self) -> tuple[tuple[str, str], ...]:
        epoch = "multi-epoch" if self.multi_epoch_local_count else "single-epoch"
        exact = "|".join((
            self.classification, self.dominant_fault, self.size_band,
            self.cycle_band, epoch,
        ))
        return (
            ("exact", exact),
            ("classification", self.classification),
            ("dominant-fault", self.dominant_fault),
            ("global", "*"),
        )

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row.update({
            "dominant_fault": self.dominant_fault,
            "size_band": self.size_band,
            "cycle_band": self.cycle_band,
        })
        return row


@dataclass(frozen=True)
class Transition:
    parent_attempt_id: int
    child_attempt_id: int
    function: str
    relation: str
    action: str
    action_family: str
    before: ResidualState
    child_compiled: bool
    child_exact: bool
    child_score_delta: float | None
    residual_changed: bool
    vector_improved: bool
    vector_regressed: bool
    cycle_changed: bool
    fault_reduction: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["before"] = self.before.to_dict()
        return row


@dataclass(frozen=True)
class PolicyEstimate:
    action_family: str
    context_tier: str
    context_key: str
    support_transitions: int
    support_functions: int
    exact_transitions: int
    compile_probability: float
    exact_probability: float
    vector_improve_probability: float
    vector_regress_probability: float
    residual_movement_probability: float
    directional_alignment: float
    mean_score_delta: float | None
    expected_fault_reduction: dict[str, float]

    @property
    def transferable(self) -> bool:
        return self.support_functions > 0

    def rank_key(self) -> tuple[float, ...]:
        """Lexicographic ranking; no scalar byte-loss weights are invented."""
        return (
            # Exactness is terminal, but use the smoothed empirical rate—not
            # a Boolean "ever exact" trophy.  One success buried among a
            # thousand regressions must not dominate a healthier family.
            self.exact_probability,
            self.vector_improve_probability,
            self.directional_alignment,
            -self.vector_regress_probability,
            self.residual_movement_probability,
            self.compile_probability,
            float(self.transferable),
            min(self.support_functions, 8) / 8.0,
            min(self.support_transitions, 64) / 64.0,
        )

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["transferable"] = self.transferable
        row["rank_key"] = list(self.rank_key())
        return row


def _faults(diff: str, *, score: float = 0.0,
            exact: bool = False) -> tuple[signals.Signals, dict[str, int]]:
    sig = signals.analyse(diff or "", score=score, exact=exact, compiled=True)
    return sig, {
        "structural": sig.structural,
        "layout": sig.layout,
        "offset": sig.offset,
        "width": sig.width,
        "relocation": sig.reloc,
        "register_allocation": sig.regalloc,
        "ordering": sig.ordering,
        "immediate": sig.immediate,
    }


def residual_state(diff: str, source: str = "", *, score: float = 0.0,
                   exact: bool = False, _gradient: Any | None = None
                   ) -> ResidualState:
    sig, faults = _faults(diff, score=score, exact=exact)
    gradient = _gradient or exactness_gradient.build(diff or "", source)
    masked = c89._mask(source)
    declarations = [
        match for line in masked.splitlines()
        if (match := c89.DECL_RE.match(line)) is not None
    ]
    cycles = gradient.register_cycles
    return ResidualState(
        classification=gradient.residual_classification,
        faults=faults,
        diff_lines=sig.diff_lines,
        instruction_delta=sig.instr_delta,
        cycle_count=len(cycles),
        largest_cycle=max((len(row.registers) for row in cycles), default=0),
        source_lines=len(source.splitlines()),
        local_count=len(declarations),
        pointer_local_count=sum(bool(row.group("ptr").strip())
                                for row in declarations),
        loop_count=len(re.findall(r"\b(?:for|while|do)\b", masked)),
        branch_count=len(re.findall(r"\b(?:if|switch)\s*\(", masked)),
        multi_epoch_local_count=len(gradient.epoch_probe_candidates),
    )


def action_family(relation: str, action: str) -> str:
    """Stable, deliberately coarse family for heterogeneous historical labels."""
    text = f"{relation} {action}".lower()
    if "split" in text and "epoch" in text:
        return "split-value-epoch"
    if "statement-order" in text or "move statement" in text or \
            "swap-independent" in text or \
            "independent-statement" in text or \
            "prologue initializer" in text:
        return "statement-order"
    if "materialize assignment web" in text or "transparent" in text:
        return "transparent-copy/materialized-web"
    if "materialize-aliased-rhs" in text:
        return "early-load/materialized-rhs"
    if "materialize-return" in text:
        return "return-value-materialization"
    if ("fuse-load-return-postincrement" in text or
            "fuse-store-load-return-postincrement" in text):
        return "pointer-postincrement-web"
    if "inline-early-temp-before-disjoint-write" in text:
        return "early-load/inlined-store"
    if "reuse-parameter-" in text:
        return "parameter-reuse"
    if "declaration" in text or "initializer" in text:
        return "declaration/initializer"
    if "register-" in text or "remove-register" in text:
        return "register-qualifier"
    if "scope" in text:
        return "scope"
    if "fuse-byte-update-lookup" in text:
        return "expression-fusion"
    if "combine-load-increment" in text:
        return "load-increment-web"
    if "direct-byte-" in text:
        return "direct-byte-update"
    if "value-web" in text or "postincrement" in text or \
            "preincrement" in text or "add-assign" in text or \
            "expanded-add" in text:
        return "value-web-shape"
    if "pointer-lifetime" in text:
        return "pointer-lifetime"
    if "pointer" in text or "store-index" in text or \
            "displacement" in text:
        return "pointer/store-shape"
    if "layout" in text or "padding" in text or "field" in text:
        return "layout"
    if "signed" in text or "width" in text or "type" in text:
        return "type/width"
    if "loop" in text or "branch" in text or "control" in text or \
            "cfg" in text:
        return "control-flow"
    if "m2c" in text:
        return "m2c-adaptation"
    if "model" in text or "causal-repair" in text or \
            "tool-agent" in text:
        return "model-proposed"
    return relation or "other"


def _vector_outcome(before: Mapping[str, int], after: Mapping[str, int],
                    child_exact: bool) -> tuple[bool, bool, dict[str, float]]:
    reductions = {
        axis: float(before.get(axis, 0) - after.get(axis, 0))
        for axis in FAULT_AXES
    }
    improved = child_exact or (
        any(value > 0 for value in reductions.values()) and
        all(value >= 0 for value in reductions.values())
    )
    regressed = (
        any(value < 0 for value in reductions.values()) and
        not any(value > 0 for value in reductions.values())
    )
    return improved, regressed, reductions


def load_transitions(conn: sqlite3.Connection, *,
                     relations: Iterable[str] | None = None,
                     limit: int | None = None) -> tuple[Transition, ...]:
    """Reconstruct source-changing transitions from explicit DAG edges."""
    tables = {str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"functions", "attempts", "attempt_edges"} <= tables:
        return ()
    parameters: list[Any] = []
    where = [
        "p.compiled=1",
        "length(e.action)>0",
        # Old attempts predate source_sha256.  Source text is authoritative
        # here and prevents both false exclusions and same-source root edges.
        "p.source_code <> ch.source_code",
        "p.func_addr = ch.func_addr",
        "p.diff_summary is not null",
    ]
    relation_list = tuple(relations or ())
    if relation_list:
        where.append("e.relation in (" + ",".join("?" for _ in relation_list) + ")")
        parameters.extend(relation_list)
    sql = (
        "SELECT p.id,ch.id,f.name,e.relation,e.action,p.source_code,"
        "p.diff_summary,p.score,p.exact,ch.source_code,ch.diff_summary,"
        "ch.compiled,ch.score,ch.exact "
        "FROM attempt_edges e "
        "JOIN attempts p ON p.id=e.parent_attempt_id "
        "JOIN attempts ch ON ch.id=e.child_attempt_id "
        "JOIN functions f ON f.addr=p.func_addr "
        "WHERE " + " AND ".join(where) + " ORDER BY p.id,ch.id"
    )
    if limit is not None:
        sql += " LIMIT ?"
        parameters.append(max(0, int(limit)))

    state_cache: dict[int, ResidualState] = {}
    before_cycle_cache: dict[int, tuple[tuple[str, ...], ...]] = {}
    after_cache: dict[int, tuple[dict[str, int], tuple[tuple[str, ...], ...]]] = {}
    transitions: list[Transition] = []
    for row in conn.execute(sql, parameters):
        (parent_id, child_id, function, relation, action, parent_source,
         parent_diff, parent_score, parent_exact, child_source, child_diff,
         child_compiled, child_score, child_exact) = row
        parent_id, child_id = int(parent_id), int(child_id)
        before = state_cache.get(parent_id)
        if before is None:
            parent_gradient = exactness_gradient.build(
                str(parent_diff or ""), str(parent_source or ""))
            before = residual_state(
                str(parent_diff or ""), str(parent_source or ""),
                score=float(parent_score or 0.0), exact=bool(parent_exact),
                _gradient=parent_gradient)
            state_cache[parent_id] = before
            before_cycle_cache[parent_id] = tuple(
                cycle.registers for cycle in parent_gradient.register_cycles)

        compiled = bool(child_compiled)
        exact = bool(child_exact)
        if compiled:
            after = after_cache.get(child_id)
            if after is None:
                _, after_faults = _faults(
                    str(child_diff or ""), score=float(child_score or 0.0),
                    exact=exact)
                after_cycles = tuple(
                    cycle.registers for cycle in
                    exactness_gradient.register_cycles(
                        exactness_gradient.register_correspondences(
                            str(child_diff or ""))))
                after = after_faults, after_cycles
                after_cache[child_id] = after
            after_faults, after_cycles = after
            improved, regressed, reductions = _vector_outcome(
                before.faults, after_faults, exact)
            before_cycles = before_cycle_cache[parent_id]
            cycle_changed = before_cycles != after_cycles
            residual_changed = residual_content(parent_diff) != residual_content(child_diff)
            score_delta = float(child_score) - float(parent_score) \
                if child_score is not None and parent_score is not None else None
        else:
            improved = regressed = cycle_changed = residual_changed = False
            reductions = {axis: 0.0 for axis in FAULT_AXES}
            score_delta = None
        transitions.append(Transition(
            parent_attempt_id=parent_id,
            child_attempt_id=child_id,
            function=str(function), relation=str(relation), action=str(action),
            action_family=action_family(str(relation), str(action)),
            before=before, child_compiled=compiled, child_exact=exact,
            child_score_delta=score_delta, residual_changed=residual_changed,
            vector_improved=improved, vector_regressed=regressed,
            cycle_changed=cycle_changed, fault_reduction=reductions,
        ))
    return tuple(transitions)


def _estimate(family: str, tier: str, key: str,
              rows: list[Transition], state: ResidualState) -> PolicyEstimate:
    count = len(rows)
    functions = len({row.function for row in rows})
    compiled_rows = [row for row in rows if row.child_compiled]
    compiled = len(compiled_rows)
    exact = sum(row.child_exact for row in rows)
    improved = sum(row.vector_improved for row in rows)
    regressed = sum(row.vector_regressed for row in rows)
    moved = sum(row.residual_changed for row in rows)
    # Weak beta priors keep one lucky probe from becoming certainty while the
    # distinct-function count remains visible as transfer support.
    compile_probability = (compiled + 1.0) / (count + 2.0)
    exact_probability = (exact + 0.1) / (count + 10.0)
    vector_probability = (improved + 1.0) / (count + 3.0)
    regress_probability = (regressed + 1.0) / (count + 3.0)
    movement_probability = (moved + 1.0) / (count + 2.0)
    expected = {
        axis: (sum(row.fault_reduction[axis] for row in compiled_rows) /
               compiled if compiled else 0.0)
        for axis in FAULT_AXES
    }
    active_axes = [axis for axis in FAULT_AXES if state.faults.get(axis, 0)]
    alignment = (
        sum(max(-1.0, min(1.0, expected[axis] /
                          max(1, state.faults[axis])))
            for axis in active_axes) / len(active_axes)
        if active_axes else 0.0
    )
    score_deltas = [row.child_score_delta for row in compiled_rows
                    if row.child_score_delta is not None]
    return PolicyEstimate(
        action_family=family, context_tier=tier, context_key=key,
        support_transitions=count, support_functions=functions,
        exact_transitions=exact,
        compile_probability=compile_probability,
        exact_probability=exact_probability,
        vector_improve_probability=vector_probability,
        vector_regress_probability=regress_probability,
        residual_movement_probability=movement_probability,
        directional_alignment=alignment,
        mean_score_delta=(sum(score_deltas) / len(score_deltas)
                          if score_deltas else None),
        expected_fault_reduction=expected,
    )


class TransitionPolicy:
    def __init__(self, transitions: Iterable[Transition]):
        self.transitions = tuple(transitions)
        grouped: dict[str, list[Transition]] = defaultdict(list)
        for row in self.transitions:
            grouped[row.action_family].append(row)
        self._by_family = {
            family: tuple(rows) for family, rows in grouped.items()
        }

    @classmethod
    def from_db(cls, conn: sqlite3.Connection, *,
                relations: Iterable[str] | None = None,
                limit: int | None = None) -> "TransitionPolicy":
        return cls(load_transitions(conn, relations=relations, limit=limit))

    def estimate(self, action: str, state: ResidualState, *,
                 relation: str = "", exclude_function: str = ""
                 ) -> PolicyEstimate:
        family = action_family(relation, action)
        eligible = [
            row for row in self._by_family.get(family, ())
            if not exclude_function or row.function != exclude_function
        ]
        for tier, key in state.context_keys():
            if tier == "global":
                selected = eligible
            else:
                selected = [
                    row for row in eligible
                    if dict(row.before.context_keys()).get(tier) == key
                ]
            if selected:
                return _estimate(family, tier, key, selected, state)
        return _estimate(family, "unseen", "", [], state)

    def rank(self, actions: Iterable[str], state: ResidualState, *,
             relation: str = "", exclude_function: str = ""
             ) -> tuple[tuple[str, PolicyEstimate], ...]:
        rows = [(action, self.estimate(
            action, state, relation=relation,
            exclude_function=exclude_function)) for action in actions]
        # Python's stable sort preserves generator order for indistinguishable
        # or unseen actions, which is the exploration fallback.
        rows.sort(key=lambda row: row[1].rank_key(), reverse=True)
        return tuple(rows)

    def summary(self, *, exclude_function: str = "") -> dict[str, Any]:
        eligible = [row for row in self.transitions
                    if not exclude_function or row.function != exclude_function]
        families = sorted({row.action_family for row in eligible})
        global_state = ResidualState(
            classification="summary", faults={}, diff_lines=0,
            instruction_delta=0, cycle_count=0, largest_cycle=0,
            source_lines=0, local_count=0, pointer_local_count=0,
            loop_count=0, branch_count=0, multi_epoch_local_count=0)
        weights = []
        for family in families:
            rows = [row for row in eligible if row.action_family == family]
            estimate = _estimate(
                family, "global", "*", rows, global_state).to_dict()
            estimate["exact_examples"] = [
                {
                    "function": row.function,
                    "action": row.action,
                    "parent_attempt_id": row.parent_attempt_id,
                    "child_attempt_id": row.child_attempt_id,
                }
                for row in rows if row.child_exact
            ][:5]
            weights.append(estimate)
        return {
            "model_version": MODEL_VERSION,
            "transition_count": len(eligible),
            "function_count": len({row.function for row in eligible}),
            "action_family_count": len(families),
            "excluded_function": exclude_function or None,
            "weights": weights,
            "authority": (
                "ranking only; compilation, semantic replay, and exact object "
                "verification remain authoritative"),
        }

    def cross_validate(self) -> dict[str, Any]:
        """Replay historical sibling panels with their function held out.

        This is an observational routing check, not a causal benchmark: old
        runs did not try every family at every parent.  Requiring sibling
        families under one explicit parent at least asks the policy the same
        bounded question the live pilot asks: which available family goes
        first?
        """
        groups: dict[tuple[str, int], list[Transition]] = defaultdict(list)
        for row in self.transitions:
            groups[(row.function, row.parent_attempt_id)].append(row)

        multi_family = decisive = covered_decisive = 0
        policy_successes = chronological_successes = 0
        first_success_ranks: list[int] = []
        exact_opportunities = policy_exact_successes = 0
        for (function, _parent_id), rows in groups.items():
            representatives: dict[str, Transition] = {}
            outcomes: dict[str, dict[str, bool]] = {}
            for row in sorted(rows, key=lambda item: item.child_attempt_id):
                representatives.setdefault(row.action_family, row)
                outcome = outcomes.setdefault(
                    row.action_family, {"success": False, "exact": False})
                outcome["success"] |= row.child_exact or row.vector_improved
                outcome["exact"] |= row.child_exact
            if len(representatives) < 2:
                continue
            multi_family += 1
            ordered = list(representatives.values())
            estimates = {
                row.action_family: self.estimate(
                    row.action, row.before, relation=row.relation,
                    exclude_function=function)
                for row in ordered
            }
            ranked = sorted(
                ordered,
                key=lambda row: estimates[row.action_family].rank_key(),
                reverse=True)
            successful = {family for family, value in outcomes.items()
                          if value["success"]}
            if successful and len(successful) < len(outcomes):
                decisive += 1
                if any(estimate.transferable for estimate in estimates.values()):
                    covered_decisive += 1
                policy_successes += ranked[0].action_family in successful
                chronological_successes += \
                    ordered[0].action_family in successful
                first_success_ranks.append(next(
                    index for index, row in enumerate(ranked, start=1)
                    if row.action_family in successful))
            exact_families = {family for family, value in outcomes.items()
                              if value["exact"]}
            if exact_families and len(exact_families) < len(outcomes):
                exact_opportunities += 1
                policy_exact_successes += \
                    ranked[0].action_family in exact_families

        return {
            "method": "leave-one-function-out sibling-family replay",
            "multi_family_parent_count": multi_family,
            "decisive_parent_count": decisive,
            "covered_decisive_parent_count": covered_decisive,
            "policy_top1_success_count": policy_successes,
            "policy_top1_success_rate": (
                policy_successes / decisive if decisive else None),
            "chronological_top1_success_count": chronological_successes,
            "chronological_top1_success_rate": (
                chronological_successes / decisive if decisive else None),
            "mean_policy_rank_of_first_success": (
                sum(first_success_ranks) / len(first_success_ranks)
                if first_success_ranks else None),
            "exact_opportunity_count": exact_opportunities,
            "policy_top1_exact_count": policy_exact_successes,
            "policy_top1_exact_rate": (
                policy_exact_successes / exact_opportunities
                if exact_opportunities else None),
            "limitations": (
                "observational historical panels; missing action families are "
                "not counterfactual failures, and semantic status was not "
                "uniformly stored in old attempt rows"),
        }


def render_summary(policy: TransitionPolicy, *,
                   exclude_function: str = "") -> str:
    return json.dumps(
        policy.summary(exclude_function=exclude_function),
        indent=2, sort_keys=True)

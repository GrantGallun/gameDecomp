"""Gradient beam search over register-allocation mutations, independent of how candidates compile.

`search(function, source, compile_candidate, target_dump)` proposes
`solver.regalloc_mutations.variants`, compiles each through the caller's
`compile_candidate(source, label) -> Compiled`, and ranks the results by the
`solver.regalloc_signature` gradient. Moves that tie on the gradient are allowed
as plateau steps, so two-step fixes stay reachable. The search stops at the first
object-exact candidate. Exactness is the caller's oracle, never the gradient.

Measured on 2026-09-13 (`eval/results/regalloc-20260913/`): run offline, this
reached 73 of the 78 register-only pending functions and 63 of 145 functions with
at most two other faults, with no model involved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from solver import regalloc_mutations, regalloc_signature


@dataclass
class Compiled:
    compiled: bool
    exact: bool
    dump: str | None = None          # normalized object dump of the candidate
    diff: str = ""


@dataclass
class Outcome:
    exact: bool
    best_source: str
    best_label: str
    baseline_gradient: tuple | None
    best_gradient: tuple | None
    compiles: int
    log: list[dict] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return self.exact or (self.best_gradient is not None and self.baseline_gradient is not None
                              and self.best_gradient < self.baseline_gradient)

    def summary(self) -> dict:
        return {"exact": self.exact, "best_label": self.best_label, "compiles": self.compiles,
                "baseline_gradient": list(self.baseline_gradient) if self.baseline_gradient else None,
                "best_gradient": list(self.best_gradient) if self.best_gradient else None}


def register_dominant(faults: dict, max_other: int | None = None) -> bool:
    """Register allocation is the largest residual fault class (and, optionally, few others remain)."""
    faults = faults or {}
    if not faults.get("register_allocation"):
        return False
    other = sum(v for k, v in faults.items() if k != "register_allocation")
    if max_other is not None and other > max_other:
        return False
    return max(faults, key=lambda k: faults[k]) == "register_allocation"


def _key(report):
    return tuple(report.gradient) if report is not None else (10**9, 0, 0)


def search(function: str, source: str, compile_candidate, target_dump: str, *,
           budget: int = 300, beam: int = 3, depth: int = 4, baseline: Compiled | None = None) -> Outcome:
    base = baseline or compile_candidate(source, "baseline")
    base_report = regalloc_signature.compare(target_dump, base.dump) if base.compiled and base.dump else None
    outcome = Outcome(base.exact, source, "baseline", _key(base_report) if base_report else None, None, 0 if baseline else 1)
    if base.exact or base_report is None:
        outcome.best_gradient = outcome.baseline_gradient
        return outcome
    seen = {source}
    frontier = [(base_report, base, source, "baseline")]
    best = (base_report, source, "baseline")
    for level in range(1, depth + 1):
        improving, sideways = [], []
        for parent_report, parent, parent_source, parent_label in frontier:
            for label, kind, variant in regalloc_mutations.variants(parent_source, function, parent.diff):
                if outcome.compiles >= budget:
                    break
                if variant in seen:
                    continue
                seen.add(variant)
                result = compile_candidate(variant, label)
                outcome.compiles += 1
                row = {"depth": level, "family": kind, "label": label, "parent": parent_label,
                       "compiled": result.compiled, "exact": result.exact}
                if result.exact:
                    outcome.log.append(row)
                    outcome.exact, outcome.best_source, outcome.best_label = True, variant, label
                    outcome.best_gradient = (0, 0, 0)
                    return outcome
                if not result.compiled or not result.dump:
                    outcome.log.append(row)
                    continue
                report = regalloc_signature.compare(target_dump, result.dump)
                row["gradient"] = list(report.gradient)
                outcome.log.append(row)
                if _key(report) < _key(parent_report):
                    improving.append((report, result, variant, label))
                elif _key(report) == _key(parent_report):
                    changed = report.signatures != parent_report.signatures
                    sideways.append((0 if changed else 1, len(sideways), (report, result, variant, label)))
                if _key(report) < _key(best[0]):
                    best = (report, variant, label)
        chosen = sorted(improving, key=lambda item: _key(item[0]))[:beam]
        chosen += [item for _p, _i, item in sorted(sideways, key=lambda s: s[:2])][:max(0, beam - len(chosen))]
        if not chosen or outcome.compiles >= budget:
            break
        frontier = chosen
    outcome.best_source, outcome.best_label = best[1], best[2]
    outcome.best_gradient = _key(best[0])
    return outcome

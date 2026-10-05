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

TRACE GUIDANCE (optional). `trace(source, label, compiled) -> report | None` returns a
`solver.uopt_diagnosis.diagnose` report for a compiled candidate (the caller owns the traced
compile). The baseline and each frontier member are diagnosed, and their children are generated
with the families preferred for the diagnosed first decision tried first. Only the order in which
variants are tried changes: the ranking is still the gradient and exactness is still the oracle.
Trace calls are counted separately (`trace_calls`) and capped by `trace_budget`.

DIVERSE BEAM (optional, `diverse=True`). The beam takes the best candidate from each mutation
family before a second candidate from any family, first among improving moves and then among
sideways moves. Motivating residual: updateEndingCreditsSlashRisingStar, where a new
`typed_reread` candidate plus two tied `local_type` variants filled the depth-1 beam and pushed
out `stmt_move:3->5`, the parent from which the older generator set reached exact.

ENABLING ROOTS (optional, `enable=True`). `regalloc_mutations.enabling_variants` are edits that leave
the gradient unchanged but let another family fire, so they never win a beam slot. When the starting
source has any, the ordinary search keeps half the budget, then each root no worse than the baseline is
searched on its own with the rest. Sharing one frontier did not work: the baseline's children used the
budget first (updateRacePlayerMode16AerialTrick stalled at 300 compiles; searched alone, its root was
exact in 83). Motivating residual: the AerialTrick family (2026-09-15 progress census).

MUTATION SELECTION (opt-in, ``selection="mutation_count"`` or ``"evolvability"``).
The existing beam retains sources by their bounded generator neighbourhood,
including worse-gradient intermediates. Evolvability additionally samples normal
counted child evaluations; samples are reused if the parent is selected. Best
result and expansion priority are separate. These are experimental heuristics,
not evidence of campaign yield. Defaults preserve the gradient policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from itertools import chain, islice

from solver import regalloc_mutations, regalloc_signature


@dataclass
class Compiled:
    compiled: bool
    exact: bool
    dump: str | None = None          # normalized object dump of the candidate
    diff: str = ""
    evidence: dict | None = None     # source_attribution / frontend of this compile, for regalloc_mutations.variants
    keyed: bool = False              # another source's compile, reused because the optimizer keys matched
    obj: bytes | None = None         # the compiled object, for certificate comparison of reused results


@dataclass
class Outcome:
    exact: bool
    best_source: str
    best_label: str
    baseline_gradient: tuple | None
    best_gradient: tuple | None
    compiles: int
    log: list[dict] = field(default_factory=list)
    trace_calls: int = 0
    first_decisions: list[str] = field(default_factory=list)   # diagnosed class of each traced parent
    key_calls: int = 0               # optimizer keys computed (charged to the budget at key_cost each)
    keyed: int = 0                   # candidates resolved from a key match instead of a compile
    reuse_events: int = 0            # key matches (keyed + audited)
    audited: int = 0                 # key matches compiled for real anyway, sampled at audit_rate
    audit_conclusive: int = 0        # audits whose comparison was conclusive (agree or differ)
    audit_unavailable: int = 0       # audits that could not be compared (not agreement)
    expansion_checks: int = 0        # reused parents compiled for real before expansion (a SELECTED subset)
    expansion_conclusive: int = 0
    key_violations: int = 0          # conclusive disagreements, any source
    key_disabled: str = ""           # why reuse stopped, if it did
    decisions: list[dict] = field(default_factory=list)
    preview_capped: int = 0          # bounded mutation previews with an unknown tail
    generation_capped: int = 0       # expanded streams cut off by the finite proposal bound

    @property
    def improved(self) -> bool:
        return self.exact or (self.best_gradient is not None and self.baseline_gradient is not None
                              and self.best_gradient < self.baseline_gradient)

    def summary(self) -> dict:
        return {"exact": self.exact, "best_label": self.best_label, "compiles": self.compiles,
                "baseline_gradient": list(self.baseline_gradient) if self.baseline_gradient else None,
                "best_gradient": list(self.best_gradient) if self.best_gradient else None,
                "trace_calls": self.trace_calls, "first_decisions": self.first_decisions,
                "key_calls": self.key_calls, "keyed": self.keyed, "reuse_events": self.reuse_events,
                "audited": self.audited, "audit_conclusive": self.audit_conclusive,
                "audit_unavailable": self.audit_unavailable, "expansion_checks": self.expansion_checks,
                "expansion_conclusive": self.expansion_conclusive, "key_violations": self.key_violations,
                "key_disabled": self.key_disabled, "preview_capped": self.preview_capped,
                "generation_capped": self.generation_capped}


def register_dominant(faults: dict, max_other: int | None = None) -> bool:
    """Register allocation is the largest residual fault class (and, optionally, few others remain)."""
    faults = faults or {}
    if not faults.get("register_allocation"):
        return False
    other = sum(v for k, v in faults.items() if k != "register_allocation")
    if max_other is not None and other > max_other:
        return False
    # Ties go to register allocation: equal "structural" counts are usually its reorders.
    return faults["register_allocation"] >= max(faults.values())


def _key(report):
    return tuple(report.gradient) if report is not None else (10**9, 0, 0)


def rank_by_sites(parent_source: str, function: str, parent: Compiled, variants: list) -> list:
    """Try variants that edit a line owning a mismatched instruction first, then variants that touch
    only instruction-less lines, then the rest; generator order is kept within each class.

    Measured on 40,000 campaign edits (eval/results/refinement-data-20260927/analysis/edit_locality.out),
    score-up rate for register-search edits: owner lines 5.9%, instruction-less lines 4.8%, other
    instruction-emitting lines 3.1%. An ORDER, never a filter: non-owner edits still carried ~6% of the
    sampled edits that reached an exact. Declines (order unchanged) without a verified attribution."""
    from solver import residual_sites
    from solver.source_attribution import instructions_of
    attribution = (parent.evidence or {}).get("source_attribution")
    try:
        mapping = residual_sites.source_map(parent_source, function, parent.diff or "", attribution)
    except Exception:
        return variants
    if mapping.get("direct_attribution_status") != "verified":
        return variants
    owners = [s for s in mapping["sites"] if s["evidence"] == "direct-compiler-line"]
    offsets = [0]
    for line in parent_source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    live = [{"start": offsets[n - 1], "stop": offsets[n]}
            for n in {r.get("candidate_line") for r in instructions_of(attribution)}
            if isinstance(n, int) and 1 <= n < len(offsets)]

    def cls(item):
        region = residual_sites.edit_region(parent_source, item[2])
        if any(residual_sites._overlap(region, s) for s in owners):
            return 0
        if not any(residual_sites._overlap(region, s) for s in live):
            return 1
        return 2
    return sorted(variants, key=cls)


def _diverse(ranked: list, slots: int, families: dict) -> list:
    """The best candidate of each family in rank order, then second-best candidates in rank order."""
    firsts, rest, seen = [], [], set()
    for item in ranked:
        kind = families[item[3]]
        (rest if kind in seen else firsts).append(item)
        seen.add(kind)
    return (firsts + rest)[:max(0, slots)]


class _KeyViolation(Exception):
    """A reused result conclusively differs from a real compile: every ranking made from reused results
    is suspect, so the search restarts without reuse (review 2026-09-28, docs/claude-review-followup-20260928.md §2)."""

    def __init__(self, outcome, base):
        super().__init__("key violation")
        self.outcome, self.base = outcome, base


_RESTART_COUNTERS = ("key_calls", "keyed", "reuse_events", "audited", "audit_conclusive", "audit_unavailable",
                     "expansion_checks", "expansion_conclusive", "key_violations", "trace_calls", "preview_capped",
                     "generation_capped")


def search(function: str, source: str, compile_candidate, target_dump: str, *,
           budget: int = 300, beam: int = 3, depth: int = 4, baseline: Compiled | None = None,
           trace=None, trace_budget: int | None = None, diverse: bool = False, enable: bool = False,
           compile_with_parent=None, key=None, key_cost: float = 0.14, resolved=None,
           rank_sites: bool = False, same_object=None, audit_rate: float = 0.0, audit_seed: int = 0,
           selection: str = "gradient", mutation_preview: int = 64, mutation_probes: int = 2,
           explore_rate: float = 0.2, selection_seed: int = 0, coalesce: bool = False,
           scoped_fields: bool = True, narrow_updates: bool = False) -> Outcome:
    """Register search; see `_search`. On a conclusive key violation the rest of the budget is spent on a
    search from the same baseline with reuse disabled; both runs' counters and logs are kept."""
    if selection not in {"gradient", "mutation_count", "evolvability"}:
        raise ValueError("unknown register search selection policy")
    if type(mutation_preview) is not int or mutation_preview < 1:
        raise ValueError("mutation_preview must be positive")
    if type(mutation_probes) is not int or mutation_probes < 0:
        raise ValueError("mutation_probes must be nonnegative")
    if not 0 <= explore_rate <= 1:
        raise ValueError("explore_rate must be in [0, 1]")
    options = dict(budget=budget, beam=beam, depth=depth, baseline=baseline, trace=trace,
                   trace_budget=trace_budget, diverse=diverse, enable=enable,
                   compile_with_parent=compile_with_parent, key=key, key_cost=key_cost, resolved=resolved,
                   rank_sites=rank_sites, same_object=same_object, audit_rate=audit_rate, audit_seed=audit_seed,
                   selection=selection, mutation_preview=mutation_preview, mutation_probes=mutation_probes,
                   explore_rate=explore_rate, selection_seed=selection_seed, coalesce=coalesce,
                   scoped_fields=scoped_fields, narrow_updates=narrow_updates)
    try:
        return _search(function, source, compile_candidate, target_dump, **options)
    except _KeyViolation as violation:
        first = violation.outcome
        spent = first.compiles + first.key_calls * key_cost
        options.update(budget=max(0.0, budget - spent), baseline=violation.base, key=None, resolved=None,
                       audit_rate=0.0)
        rerun = _search(function, source, compile_candidate, target_dump, **options)
        rerun.compiles += first.compiles
        for name in _RESTART_COUNTERS:
            setattr(rerun, name, getattr(rerun, name) + getattr(first, name))
        rerun.first_decisions = first.first_decisions + rerun.first_decisions
        rerun.log = first.log + [{"depth": -1, "label": "restart-without-key-reuse", "key_restart": True}] + rerun.log
        rerun.decisions = first.decisions + rerun.decisions
        rerun.key_disabled = "restarted without reuse after a conclusive key violation"
        rerun.baseline_gradient = first.baseline_gradient
        return rerun


def _search(function: str, source: str, compile_candidate, target_dump: str, *,
            budget: float, beam: int, depth: int, baseline: Compiled | None, trace, trace_budget: int | None,
            diverse: bool, enable: bool, compile_with_parent, key, key_cost: float, resolved, rank_sites: bool,
            same_object, audit_rate: float, audit_seed: int, selection: str, mutation_preview: int,
            mutation_probes: int, explore_rate: float, selection_seed: int, coalesce: bool,
            scoped_fields: bool, narrow_updates: bool) -> Outcome:
    """Search with optional ``compile_with_parent(source, label, parent_source)`` lineage.

    KEYED RESOLUTION (optional, ``key(source) -> str | None``, e.g. ``solver.ido_stages.optimizer_key``).
    A candidate whose key equals one already compiled in this search takes that compile's result
    instead of being compiled: same key => same object bytes, measured 174/174 on campaign pairs across
    eight strategy families and 293/293 on tree-pilot pairs (eval/results/refinement-data-20260927/
    analysis/noop_definition_check.out, optimizer_key_probe.out). 37.1% of logged register-search
    children compiled to their parent's object. Ranking is unchanged, so plateau steps and enabling
    roots are still taken; a resolved candidate chosen to EXPAND is compiled for real first, because the
    generators read its own source attribution. Each key costs ``key_cost`` compiles of budget (0.034 s
    vs 0.243 s measured). ``resolved(candidate, label, parent_source, same_as_source)`` lets the caller
    log candidates that were never compiled.

    CHECKING REUSE. Two checks compile a reused candidate for real and compare it with the compile it
    reused: every reused parent chosen to expand (a SELECTED subset), and a seeded random sample of reuse
    events at ``audit_rate`` (charged to the budget). ``same_object(prior, actual) -> bool | None`` should
    be an object certificate (``byte_certificate.certify`` on ``Compiled.obj``); without one the listing is
    compared, which is weaker. Outcomes: a conclusive difference restarts the search without reuse (see
    `search`); an unavailable comparison or failed compile is not agreement and stops reuse for the rest
    of the search; a real compile that is exact is recorded as the match."""
    import random
    known: dict = {}                 # optimizer key -> (Compiled, source) of a real compile
    origin: dict = {}                # candidate source -> the parent source it was generated from
    reuse = [key is not None]        # cleared when a check is inconclusive
    rng = random.Random(audit_seed)
    selection_rng = random.Random(selection_seed)  # probing/selection must not consume audit draws

    def spent():
        return outcome.compiles + outcome.key_calls * key_cost

    def real(candidate, label, parent_source):
        return (compile_with_parent(candidate, label, parent_source) if compile_with_parent is not None
                else compile_candidate(candidate, label))

    def agree(prior, actual):
        """True / False when conclusive, None when the comparison cannot be made."""
        if not actual.compiled:
            return None
        if actual.exact != prior.exact:
            return False
        if same_object is not None:
            try:
                return same_object(prior, actual)
            except Exception:
                return None
        return None if prior.dump is None or actual.dump is None else prior.dump == actual.dump

    def check(prior, actual, kind, label):
        verdict = agree(prior, actual)
        if kind == "audit":
            outcome.audit_conclusive += verdict is not None
            outcome.audit_unavailable += verdict is None
        else:
            outcome.expansion_checks += 1
            outcome.expansion_conclusive += verdict is not None
        if verdict is False:
            outcome.key_violations += 1
            outcome.log.append({"depth": -1, "label": label, "key_violation": True, "check": kind,
                                "compiled": actual.compiled, "exact": actual.exact})
            if not actual.exact:                        # an exact real compile is simply the match
                raise _KeyViolation(outcome, base)
        elif verdict is None and reuse[0]:
            reuse[0] = False
            outcome.key_disabled = f"{kind} check unavailable at {label}"

    def compile_one(candidate, label, parent_source):
        """(result, compiled_for_real)."""
        origin.setdefault(candidate, parent_source)
        k = None
        if key is not None and reuse[0]:
            outcome.key_calls += 1
            try:
                k = key(candidate)
            except Exception:                            # a key is an optimization; never fail the search
                k = None
            if k is not None and k in known:
                prior, prior_source = known[k]
                outcome.reuse_events += 1
                if audit_rate and rng.random() < audit_rate:
                    actual = real(candidate, label, parent_source)
                    outcome.audited += 1
                    try:
                        check(prior, actual, "audit", label)
                    except _KeyViolation:
                        # The caller normally charges this compile on return;
                        # a restart must not lose the audit that raised here.
                        outcome.compiles += 1
                        raise
                    return actual, True
                outcome.keyed += 1
                if resolved is not None:
                    resolved(candidate, label, parent_source, prior_source)
                return Compiled(prior.compiled, prior.exact, prior.dump, prior.diff, None, keyed=True,
                                obj=prior.obj), False
        result = real(candidate, label, parent_source)
        if k is not None and result.compiled and not result.exact:
            known.setdefault(k, (result, candidate))
        return result, True

    def expandable(parent, parent_source, parent_label):
        """A resolved parent is compiled for real, and checked, before its children are generated."""
        if not parent.keyed:
            return parent
        actual = real(parent_source, parent_label, origin.get(parent_source))
        outcome.compiles += 1
        check(parent, actual, "expansion", parent_label)
        return actual

    base = None
    outcome = Outcome(False, source, "baseline", None, None, 0)
    if baseline is not None:
        base = baseline
        if key is not None and base.compiled and not base.exact:
            outcome.key_calls += 1
            try:
                k = key(source)
            except Exception:
                k = None
            if k is not None:
                known[k] = (base, source)
    else:
        base, _real = compile_one(source, "baseline", None)
    base_report = regalloc_signature.compare(target_dump, base.dump) if base.compiled and base.dump else None
    outcome.exact = base.exact
    outcome.baseline_gradient = _key(base_report) if base_report else None
    outcome.compiles = 0 if baseline else 1
    if base.exact or base_report is None:
        outcome.best_gradient = outcome.baseline_gradient
        return outcome
    trace_cap = trace_budget if trace_budget is not None else 1 + beam * depth

    def prefer(candidate_source, compiled, label):
        if trace is None or outcome.trace_calls >= trace_cap:
            return ()
        outcome.trace_calls += 1
        try:
            report = trace(candidate_source, label, compiled)
        except Exception:                                   # guidance is optional; never fail the search
            report = None
        from solver import uopt_diagnosis
        first = (report or {}).get("first")
        outcome.first_decisions.append(first["class"] if first else ("declined" if not report or "declined" in report
                                                                     else "clean"))
        return uopt_diagnosis.preferred_families(report)

    best = (base_report, source, "baseline")

    def record_exact(variant, label):
        outcome.exact, outcome.best_source, outcome.best_label = True, variant, label
        outcome.best_gradient = (0, 0, 0)

    def update_best(report, variant, label):
        nonlocal best
        if _key(report) < _key(best[0]):
            best = (report, variant, label)

    def source_id(candidate):
        return hashlib.sha256(candidate.encode()).hexdigest()

    # Only experimental policies use lookahead. A probe is a normal counted
    # compile/key request, retained for expansion so it is never charged twice.
    probe_results, previews = {}, {}

    def generated_for(parent, parent_source, preferred, seen, *, preview=False):
        extra = {"evidence": parent.evidence} if parent.evidence else {}
        if coalesce:
            extra['coalesce'] = True
        if not scoped_fields:
            extra['scoped_fields'] = False
        if narrow_updates:
            extra['narrow_updates'] = True
        generated = (previews.pop(parent_source)["stream"] if parent_source in previews else
                     regalloc_mutations.variants(parent_source, function, parent.diff,
                                                 prefer=preferred, **extra))
        if preview:
            offered = list(islice(generated, mutation_preview + 1))
            capped = len(offered) > mutation_preview
            outcome.preview_capped += capped
            distinct, unique = [], {parent_source}
            for item in offered[:mutation_preview]:
                if item[2] not in unique:
                    unique.add(item[2])
                    distinct.append(item)
            if rank_sites:
                distinct = rank_by_sites(parent_source, function, parent, distinct)
            previews[parent_source] = {"stream": chain(offered, generated), "complete": not capped}
            outcome.log.append({"label": "mutation-preview", "source_sha256": source_id(parent_source),
                                "offered": len(distinct), "preview_complete": not capped})
            return [v for v in distinct if v[2] not in seen]
        if selection != "gradient":
            # Actual expansion resumes the entire stream, including the unseen
            # preview tail. A larger independent guard handles broken generators.
            limit = max(32, mutation_preview + 1, int(budget * 8))
            offered = list(islice(generated, limit + 1))
            if len(offered) > limit:
                outcome.generation_capped += 1
                outcome.log.append({"label": "generation-cap", "source_sha256": source_id(parent_source),
                                    "limit": limit})
            generated = offered[:limit]
        return rank_by_sites(parent_source, function, parent, list(generated)) if rank_sites else generated

    def choose_by_mutations(candidates, seen, cap, level):
        """Rank expandable source states, including worse ones, with bounded lookahead.

        Counts refer to a generator prefix, not the full neighbourhood. Quality
        uses offspring beating the SAME pre-probe incumbent, then distinct
        changed normalized listings, family breadth and remaining source moves.
        Listing diversity is only a heuristic, never object identity/exactness.
        """
        reference = _key(best[0])
        pool = []
        for report, parent, candidate, label, family in candidates:
            if parent.keyed:
                if spent() + 1 > cap:
                    continue
                parent = expandable(parent, candidate, label)
                if parent.exact:
                    record_exact(candidate, label)
                    return []
                if not parent.compiled or not parent.dump:
                    continue
                report = regalloc_signature.compare(target_dump, parent.dump)
                update_best(report, candidate, label)
            preferred = prefer(candidate, parent, label)
            offers = generated_for(parent, candidate, preferred, seen, preview=True)
            complete = previews[candidate]["complete"]
            if offers or not complete:
                pool.append({"item": (report, parent, candidate, label, preferred), "family": family,
                             "id": source_id(candidate), "offers": offers, "sampled": set(),
                             "complete": complete, "trials": 0, "improvements": 0,
                             "changed_listings": set()})
        # Round-robin probes avoid giving the first parent the whole sampling allowance.
        for _ in range(mutation_probes if selection == "evolvability" else 0):
            for node in pool:
                available = [v for v in node["offers"] if v[2] not in node["sampled"]]
                if not available:
                    continue
                label, family, variant = selection_rng.choice(available)
                if variant not in probe_results and spent() + 1 + (key_cost if key and reuse[0] else 0) > cap:
                    continue
                node["sampled"].add(variant)
                parent_source = node["item"][2]
                cached = variant in probe_results
                if cached:
                    result = probe_results[variant]
                else:
                    result, compiled_now = compile_one(variant, label, parent_source)
                    outcome.compiles += compiled_now
                    probe_results[variant] = result
                row = {"depth": level + 1, "label": label, "family": family,
                       "parent": node["item"][3], "probe": True, "cached_probe": cached,
                       "source_sha256": source_id(variant), "compiled": result.compiled,
                       "exact": result.exact, "keyed": result.keyed,
                       "proposal_probability": 1 / len(available)}
                outcome.log.append(row)
                node["trials"] += 1  # Includes compile failures and same-object outcomes.
                if result.exact:
                    record_exact(variant, label)
                    return []
                if result.compiled and result.dump:
                    report = regalloc_signature.compare(target_dump, result.dump)
                    row["gradient"] = list(report.gradient)
                    node["improvements"] += _key(report) < reference
                    if result.dump != node["item"][1].dump:
                        node["changed_listings"].add(result.dump)
                    update_best(report, variant, label)

        def rank(node):
            count = len(node["offers"])
            quality = (() if selection == "mutation_count" else
                       (-node["improvements"] / max(1, node["trials"]),
                        -len(node["changed_listings"]) / max(1, node["trials"]),
                        -len({v[1] for v in node["offers"]})))
            return quality + (-count, _key(node["item"][0]))

        chosen, chosen_families = [], set()
        while pool and len(chosen) < beam:
            eligible = ([n for n in pool if n["family"] not in chosen_families] if diverse else pool)
            eligible = eligible or pool
            ranked = sorted(eligible, key=rank)
            probabilities = {n["id"]: explore_rate / len(ranked) for n in ranked}
            probabilities[ranked[0]["id"]] += 1 - explore_rate
            exploring = selection_rng.random() < explore_rate
            choice = selection_rng.choice(ranked) if exploring else ranked[0]
            outcome.decisions.append({"depth": level, "policy": selection, "id": choice["id"],
                "label": choice["item"][3], "mode": "explore" if exploring else "rank",
                "probabilities": probabilities, "probability": probabilities[choice["id"]],
                "reference_gradient": list(reference),
                "candidates": [{"id": n["id"], "label": n["item"][3],
                                "pending_moves": len(n["offers"]), "sampled": n["trials"],
                                "preview_complete": n["complete"],
                                "improving_samples": n["improvements"],
                                "distinct_changed_listings": len(n["changed_listings"]),
                                "families": len({v[1] for v in n["offers"]})} for n in ranked]})
            chosen.append(choice["item"])
            chosen_families.add(choice["family"])
            pool.remove(choice)
        return chosen

    def run_beam(frontier, seen, cap):
        """Beam from `frontier` until `cap` compiles in total; True when a candidate is object-exact."""
        nonlocal best
        for level in range(1, depth + 1):
            improving, sideways, families = [], [], {}
            candidates = []
            for parent_report, parent, parent_source, parent_label, preferred in frontier:
                if spent() >= cap:
                    break
                was_reused = parent.keyed
                parent = expandable(parent, parent_source, parent_label)
                if parent.exact:                        # only if a key was wrong; the oracle still decides
                    record_exact(parent_source, parent_label)
                    return True
                if not parent.compiled or not parent.dump:
                    continue
                if was_reused and parent.compiled and parent.dump:
                    # children are ranked against the parent's OWN result, never the reused one
                    parent_report = regalloc_signature.compare(target_dump, parent.dump)
                generated = generated_for(parent, parent_source, preferred, seen)
                for label, kind, variant in generated:
                    if spent() >= cap:
                        break
                    if variant in seen:
                        continue
                    seen.add(variant)
                    cached_probe = selection != "gradient" and variant in probe_results
                    if cached_probe:
                        result, compiled_now = probe_results.pop(variant), False
                    else:
                        result, compiled_now = compile_one(variant, label, parent_source)
                    outcome.compiles += compiled_now
                    row = {"depth": level, "family": kind, "label": label, "parent": parent_label,
                           "compiled": result.compiled, "exact": result.exact}
                    if result.keyed:
                        row["keyed"] = True
                    if cached_probe:
                        row["cached_probe"] = True
                    if result.exact:
                        outcome.log.append(row)
                        record_exact(variant, label)
                        return True
                    if not result.compiled or not result.dump:
                        outcome.log.append(row)
                        continue
                    report = regalloc_signature.compare(target_dump, result.dump)
                    row["gradient"] = list(report.gradient)
                    outcome.log.append(row)
                    families[label] = kind
                    candidates.append((report, result, variant, label, kind))
                    if _key(report) < _key(parent_report):
                        improving.append((report, result, variant, label))
                    elif _key(report) == _key(parent_report):
                        changed = report.signatures != parent_report.signatures
                        sideways.append((0 if changed else 1, len(sideways), (report, result, variant, label)))
                    if _key(report) < _key(best[0]):
                        best = (report, variant, label)
            if selection != "gradient":
                if level >= depth or spent() >= cap:
                    return False
                frontier = choose_by_mutations(candidates, seen, cap, level)
                if outcome.exact:
                    return True
                if not frontier:
                    return False
                continue
            ranked_improving = sorted(improving, key=lambda item: _key(item[0]))
            ranked_sideways = [item for _p, _i, item in sorted(sideways, key=lambda s: s[:2])]
            if diverse:
                chosen = _diverse(ranked_improving, beam, families)
                chosen += _diverse(ranked_sideways, beam - len(chosen), families)
            else:
                chosen = ranked_improving[:beam]
                chosen += ranked_sideways[:max(0, beam - len(chosen))]
            if not chosen or spent() >= cap:
                return False
            frontier = [(report, result, variant, label, prefer(variant, result, label))
                        for report, result, variant, label in chosen]
        return False

    roots = list(regalloc_mutations.enabling_variants(source, function)) if enable else []
    # Phase 1: the ordinary search, holding back half the budget when there are enabling roots. Every exact of
    # the 2026-09-15 cohort arm A took at most 111 compiles, and the AerialTrick roots needed 83-88.
    first_cap = budget - (budget // 2 if roots else 0)
    if run_beam([(base_report, base, source, "baseline", prefer(source, base, "baseline"))], {source}, first_cap):
        return outcome
    # Phase 2: enabling roots of phase 1's best source first (updateRacePlayerMode31AerialTrick needed a statement
    # swap that phase 1 finds AND the enabler on top of it), then of the starting source. Each root no worse than
    # the source it came from is searched with whatever budget is left.
    parents = [(best[1], best[0])] if roots and best[1] != source else []
    parents.append((source, base_report))
    staged, offered = [], {source}
    for parent_source, parent_report in parents:
        for label, kind, variant in (regalloc_mutations.enabling_variants(parent_source, function)
                                     if parent_source != source else roots):
            if variant not in offered:
                offered.add(variant)
                staged.append((label, kind, variant, parent_report, parent_source))
    for label, kind, variant, parent_report, parent_source in staged:
        if spent() >= budget:
            break
        result, compiled_now = compile_one(variant, label, parent_source)
        outcome.compiles += compiled_now
        row = {"depth": 0, "family": kind, "label": label, "parent": "baseline",
               "compiled": result.compiled, "exact": result.exact}
        if result.keyed:
            row["keyed"] = True
        if result.exact:
            outcome.log.append(row)
            record_exact(variant, label)
            return outcome
        if not result.compiled or not result.dump:
            outcome.log.append(row)
            continue
        report = regalloc_signature.compare(target_dump, result.dump)
        row["gradient"] = list(report.gradient)
        outcome.log.append(row)
        if _key(report) > _key(parent_report):
            continue
        if _key(report) < _key(best[0]):
            best = (report, variant, label)
        if run_beam([(report, result, variant, label, prefer(variant, result, label))], {source, variant}, budget):
            return outcome
    outcome.best_source, outcome.best_label = best[1], best[2]
    outcome.best_gradient = _key(best[0])
    return outcome

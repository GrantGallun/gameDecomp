"""Opt-in, compile-budgeted search policy experiments.

The distance computed from complete listings guides expansion only. A compiler
callback owns the exact verdict; callers running real experiments must make that
verdict a source-bound object certificate and frontend pass.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import random

from solver import regalloc_mutations, regalloc_search, regalloc_signature

PRODUCTION_ARMS = {'production', 'production_diverse', 'mutation_count', 'mutation_count_diverse',
                   'evolvability', 'evolvability_diverse', 'production_coalesce', 'evolvability_coalesce'}
KEY_COST = 0.14


@dataclass
class _Node:
    id: str
    source: str
    label: str
    family: str
    parent_source: str | None
    depth: int
    compiled: regalloc_search.Compiled
    score: tuple[int, ...]
    descriptor: tuple
    worse: bool = False


def _rank(node: _Node) -> tuple:
    return node.score, node.depth, int(node.id[1:])


def _descriptor(report) -> tuple:
    """A residual cell based on the full listing, never a source spelling."""
    return (tuple(report.gradient), tuple(sorted(report.signatures.items())),
            report.reordered, report.renames)


def _archive(nodes: list[_Node], size: int) -> list[_Node]:
    """Reserve one slot per residual cell, then retain source-distinct ties."""
    ranked = sorted(nodes, key=_rank)
    first, rest, cells = [], [], set()
    for node in ranked:
        if node.descriptor in cells:
            rest.append(node)
        else:
            first.append(node)
            cells.add(node.descriptor)
    return (first + rest)[:size]


def _choose(pool: list[_Node], *, explore: bool, rate: float, rng: random.Random,
            decisions: list[dict]) -> _Node | None:
    """Choose the current best or draw a bounded worse-step candidate."""
    if not pool:
        return None
    good = sorted((n for n in pool if not n.worse), key=_rank)
    worse = sorted((n for n in pool if n.worse), key=_rank)
    probabilities = {}
    if good:
        probabilities[good[0].id] = 1.0 - rate if explore and worse else 1.0
    if explore and worse:
        probabilities.update({node.id: rate / len(worse) for node in worse})
    draw_worse = bool(explore and worse and rng.random() < rate)
    if draw_worse:
        choice = rng.choice(worse)
        mode = "explore"
    elif good:
        choice = good[0]
        mode = "rank"
    else:
        choice = None
        mode = "declined"
    none_probability = ((1.0 - rate) if explore else 1.0) if worse and not good else 0.0
    decisions.append({"id": choice.id if choice else None,
                      "source": choice.source if choice else None, "mode": mode,
                      "eligible_ids": [id_ for id_, p in probabilities.items() if p > 0],
                      "probabilities": {id_: p for id_, p in probabilities.items() if p > 0},
                      "none_probability": none_probability,
                      "probability": probabilities[choice.id] if choice else none_probability})
    return choice


def _production(function, source, target_dump, compile_candidate, *, arm, budget, beam, depth,
                seed, key, same_object, mutation_preview, mutation_probes, explore_rate, scoped_fields):
    if not callable(key) or not callable(same_object):
        raise ValueError('production arms require key and same_object callbacks')
    events, resolutions = [], []
    selection = arm.removesuffix('_coalesce').removesuffix('_diverse')
    if selection == 'production':
        selection = 'gradient'
    policy = {'engine': 'solver.regalloc_search',
              'engine_sha256': hashlib.sha256(Path(regalloc_search.__file__).read_bytes()).hexdigest(),
              'reference': 'main-tree eval.agentrepair._regalloc_search; not a frozen-checkpoint replay',
              'enable': True, 'keyed': True, 'diverse': arm.endswith('_diverse'),
              'coalesce': arm.endswith('_coalesce'), 'scoped_fields': scoped_fields,
              'key_cost': KEY_COST, 'audit_rate': 0.02, 'audit_seed': seed,
              'beam': beam, 'depth': depth, 'rank_sites': False, 'trace': False,
              'budget_basis': 'compiles + key_calls * key_cost'}
    policy.update(selection=selection, selection_seed=seed, mutation_preview=mutation_preview,
                  mutation_probes=mutation_probes if selection == 'evolvability' else 0,
                  explore_rate=explore_rate if selection != 'gradient' else 0,
                  preview_scope='bounded generator prefix; unknown tails remain eligible',
                  generation_limit=max(32, mutation_preview + 1, budget * 8) if selection != 'gradient' else None)

    def compile_logged(candidate, label, parent_source):
        row = {"id": f"s{len(events)}", "source": candidate, "label": label,
               "parent_source": parent_source}
        try:
            result = compile_candidate(candidate, label, parent_source)
            if not isinstance(result, regalloc_search.Compiled):
                raise TypeError("compiler must return Compiled")
            if not result.compiled and result.exact:
                result = regalloc_search.Compiled(False, False)
        except Exception as exc:
            result = regalloc_search.Compiled(False, False)
            row["error"] = type(exc).__name__
        row.update(compiled=bool(result.compiled), exact=bool(result.compiled and result.exact))
        if result.compiled and result.dump is not None:
            row['gradient'] = list(regalloc_signature.compare(target_dump, result.dump).gradient)
        events.append(row)
        return result

    def resolved(candidate, label, parent_source, same_as_source):
        # Reuse is not a compile or an exact receipt. Preserve source identity
        # and provenance separately from the real-compile denominator.
        resolutions.append({'source': candidate, 'label': label, 'parent_source': parent_source,
                            'same_as_source': same_as_source, 'exact': False,
                            'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                            'after_compile': len(events)})
        recorder = getattr(compile_candidate, 'record_resolution', None)
        if recorder is not None:
            recorder(resolutions[-1])

    outcome = regalloc_search.search(
        function, source, lambda candidate, label: compile_logged(candidate, label, None),
        target_dump, budget=budget, beam=beam, depth=depth,
        enable=True, diverse=policy['diverse'], compile_with_parent=compile_logged,
        key=key, key_cost=KEY_COST, resolved=resolved, same_object=same_object,
        audit_rate=policy['audit_rate'], audit_seed=seed,
        selection=selection, mutation_preview=mutation_preview, mutation_probes=mutation_probes,
        explore_rate=explore_rate, selection_seed=seed, coalesce=policy['coalesce'], scoped_fields=scoped_fields,
    )
    spent = len(events) + outcome.key_calls * KEY_COST
    return {"arm": arm, "exact": outcome.exact, "best_source": outcome.best_source,
            "compiles": len(events), "events": events, "decisions": outcome.decisions,
            "key_calls": outcome.key_calls, "budget_spent": spent,
            "budget_overshoot": max(0.0, spent - budget), "policy": policy,
            "resolutions": resolutions,
            "stop": ("exact" if outcome.exact else "budget" if spent >= budget else
                     "generation_cap" if outcome.generation_capped else "exhausted"),
            "production_log": outcome.log, "production_summary": outcome.summary()}


def run_search(function, source, target_dump, compile_candidate, *, arm="beam",
               budget=128, seed=0, beam=3, depth=4, archive_size=12,
               explore_rate=0.2, generate=None, key=None, same_object=None,
               mutation_preview=64, mutation_probes=2, scoped_fields=True) -> dict:
    """Search a frozen root with one counted callback call per compilation.

    ``generate`` receives its own source's compilation, preserving attribution
    even when two source spellings produce the same listing. Generation is capped
    in addition to compilation, so a broken infinite generator cannot hang a run.
    """
    if arm not in {"beam", "archive", "explore", "archive_explore"} | PRODUCTION_ARMS:
        raise ValueError(f"unknown search arm: {arm}")
    if not isinstance(budget, int) or budget < 0:
        raise ValueError("budget must be a nonnegative integer")
    if not isinstance(beam, int) or beam < 1 or not isinstance(depth, int) or depth < 0:
        raise ValueError("beam must be positive and depth nonnegative")
    if not isinstance(archive_size, int) or archive_size < 1:
        raise ValueError("archive_size must be positive")
    if not 0 <= explore_rate <= 1:
        raise ValueError("explore_rate must be in [0, 1]")
    result = {"arm": arm, "exact": False, "best_source": source,
              "compiles": 0, "events": [], "decisions": [], "stop": "budget",
              "policy": {"engine": "research_suite.mechanism", "scope": "mechanism-only",
                         "enable": False, "keyed": False, "scoped_fields": scoped_fields,
                         "archive_descriptor": "residual-first-with-leftover-source-ties"
                         if arm in {'archive', 'archive_explore'} else None,
                         "archive_size": archive_size, "explore_rate": explore_rate, "seed": seed}}
    if budget == 0:
        return result
    if arm in PRODUCTION_ARMS:
        if generate is not None:
            raise ValueError("production arm uses the existing solver generator")
        return _production(function, source, target_dump, compile_candidate,
                           arm=arm, budget=budget, beam=beam, depth=depth, seed=seed,
                           key=key, same_object=same_object, mutation_preview=mutation_preview,
                           mutation_probes=mutation_probes, explore_rate=explore_rate, scoped_fields=scoped_fields)

    rng = random.Random(seed)
    seen = {source}
    proposal_cap = max(32, budget * 8)
    proposals = 0
    generation_capped = False
    best_score = None

    def compile_one(candidate, label, family, parent_source, level):
        nonlocal best_score
        row = {"id": f"s{len(result['events'])}", "source": candidate,
               "label": label, "family": family, "parent_source": parent_source,
               "depth": level}
        try:
            compiled = compile_candidate(candidate, label, parent_source)
            if not isinstance(compiled, regalloc_search.Compiled):
                raise TypeError("compiler must return Compiled")
        except Exception as exc:
            compiled = regalloc_search.Compiled(False, False)
            row["error"] = type(exc).__name__
        result["compiles"] += 1
        row.update(compiled=bool(compiled.compiled), exact=bool(compiled.compiled and compiled.exact))
        score, descriptor = None, ()
        if compiled.compiled and compiled.dump is not None:
            report = regalloc_signature.compare(target_dump, compiled.dump)
            score = tuple(report.gradient)
            descriptor = _descriptor(report)
            row["gradient"] = list(score)
        result["events"].append(row)
        if row["exact"]:
            result.update(exact=True, best_source=candidate, stop="exact")
        elif score is not None and (best_score is None or score < best_score):
            best_score = score
            result["best_source"] = candidate
        if not compiled.compiled or score is None:
            return None
        return _Node(row["id"], candidate, label, family, parent_source,
                     level, compiled, score, descriptor)

    root = compile_one(source, "baseline", "baseline", None, 0)
    if result["exact"]:
        return result
    if root is None:
        result["stop"] = "root_failed"
        return result
    if depth == 0:
        result["stop"] = "depth"
        return result

    def children(parent):
        nonlocal proposals, generation_capped
        if generate is None:
            stream = regalloc_mutations.variants(
                parent.source, function, parent.compiled.diff,
                evidence=parent.compiled.evidence,
                scoped_fields=scoped_fields,
            )
        else:
            stream = generate(parent.source, parent.compiled)
        produced = []
        for label, family, candidate in stream:
            if proposals >= proposal_cap:
                generation_capped = True
                break
            proposals += 1
            if result["compiles"] >= budget or result["exact"]:
                break
            if candidate in seen:
                continue
            seen.add(candidate)
            node = compile_one(candidate, label, family, parent.source, parent.depth + 1)
            if result["exact"]:
                break
            if node is None:
                continue
            if node.score > parent.score:
                node.worse = True
            produced.append(node)
        return produced

    exploratory = arm in {"explore", "archive_explore"}
    if arm in {"beam", "explore"}:
        frontier = [root]
        for _level in range(depth):
            pool = []
            for parent in frontier:
                if result["compiles"] >= budget or result["exact"]:
                    break
                pool.extend(children(parent))
            if result["exact"]:
                return result
            next_frontier = []
            for _ in range(beam):
                choice = _choose(pool, explore=exploratory, rate=explore_rate,
                                 rng=rng, decisions=result["decisions"])
                if choice is None:
                    break
                pool.remove(choice)
                next_frontier.append(choice)
            frontier = next_frontier
            if not frontier or result["compiles"] >= budget:
                break
    else:
        pending = [root]
        while pending and result["compiles"] < budget:
            choice = _choose(pending, explore=exploratory, rate=explore_rate,
                             rng=rng, decisions=result["decisions"])
            if choice is None:
                break
            pending.remove(choice)
            if choice.depth >= depth:
                continue
            admitted = children(choice)
            if not exploratory:
                admitted = [node for node in admitted if not node.worse]
            pending = _archive(pending + admitted, archive_size)
            if result["exact"]:
                return result
    result["stop"] = ("budget" if result["compiles"] >= budget else
                      "generation_cap" if generation_capped else "exhausted")
    return result

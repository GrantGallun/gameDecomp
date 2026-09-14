"""Bounded counterexample-guided deterministic repair.

The oracle supplies a residual for every compiling C candidate. A repair round
applies one source rewrite, recompiles, and derives the next rewrites from the
*new* residual::

    compile -> residual -> rewrite -> compile -> re-derive -> ...

Two details are load bearing:

* A useful intermediate may score worse. Score ranks candidates but never
  admits or rejects them.
* Repair paths are not necessarily pairs. The old implementation stopped at
  depth two and claimed to prioritise cross-kind pairs, but the calculated
  ordering was unused. This implementation performs a bounded beam search,
  preserves one representative per rewrite kind/path shape, and deduplicates
  source before paying for another compile.

Only ``Attempt.exact`` is a success verdict. Structural signals are search
heuristics, never substitutes for the oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from solver import rewrites, signals, workspace


@dataclass
class _State:
    source: str
    attempt: workspace.Attempt
    labels: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()


def _digest(source: str) -> str:
    return sha256(source.encode("utf-8")).hexdigest()


def _state_signals(state: _State) -> signals.Signals:
    att = state.attempt
    return signals.analyse(att.diff, att.score, att.exact, att.compiled)


def _rank(state: _State) -> tuple:
    """Deterministic expansion order; exactness still comes from the oracle."""
    sig = _state_signals(state)
    # `ordering` is summed explicitly. It was carved out of layout/regalloc
    # when signals learned to recognise a displaced-but-correct instruction,
    # and a rank that enumerates kinds by name silently stops counting a fault
    # the moment it is given a name of its own -- the pass would have declined
    # on exactly the residual stmtorder owns.
    classified = (sig.layout + sig.reloc + sig.regalloc + sig.immediate
                  + sig.ordering)
    repeated_kinds = len(state.kinds) - len(set(state.kinds))
    if "stmtorder" in state.kinds:
        # On allocation-shaped residuals byte score is measured to move in the
        # opposite direction: five correct-direction swaps reduced faults
        # 41 -> 34 while score fell 91.974 -> 85.987. A structural fault costs
        # two here, matching the focused experiment: it may be a transient
        # alignment artifact, but cannot be bought one-for-one with reg moves.
        faults = 2 * sig.structural + classified
        return (faults, sig.structural, abs(sig.instr_delta),
                repeated_kinds, -sig.score, state.kinds, state.labels)
    return (sig.structural, abs(sig.instr_delta), classified,
            repeated_kinds, -sig.score, state.kinds, state.labels)


def _frontier(states: list[_State], width: int) -> list[_State]:
    """Keep good candidates without letting one rewrite kind fill the beam.

    Score-only ordering recreated the original composition bug: many plausible
    argument swaps could consume the budget before a lower-scoring layout
    intermediate was expanded. Representatives by last kind and cumulative
    kind signature make that diversity executable rather than documentary.
    """
    if width <= 0:
        return []
    ordered = sorted(states, key=_rank)
    chosen: list[_State] = []
    chosen_ids: set[str] = set()

    def add(state: _State) -> None:
        key = _digest(state.source)
        if key not in chosen_ids and len(chosen) < width:
            chosen.append(state)
            chosen_ids.add(key)

    # Diversity first. Otherwise a Pareto/score frontier can still contain
    # only one generator family and starve the interacting repair.
    by_last_kind: dict[str, _State] = {}
    by_last_kind_score: dict[str, _State] = {}
    by_path_shape: dict[tuple[str, ...], _State] = {}
    by_path_shape_score: dict[tuple[str, ...], _State] = {}
    for state in ordered:
        if state.kinds:
            by_last_kind.setdefault(state.kinds[-1], state)
            by_path_shape.setdefault(tuple(sorted(set(state.kinds))), state)
    for state in sorted(states, key=lambda item: (-item.attempt.score,
                                                   item.kinds, item.labels)):
        if state.kinds:
            by_last_kind_score.setdefault(state.kinds[-1], state)
            by_path_shape_score.setdefault(
                tuple(sorted(set(state.kinds))), state)
    for state in sorted(by_last_kind.values(), key=_rank):
        add(state)
    # Different call sites share the same kind. Keeping only the fault-best
    # argswap dropped the score-best setMainMenuSceneModelRotation swap and
    # broke a historical exact composition regression.
    for state in sorted(by_last_kind_score.values(), key=_rank):
        add(state)
    for state in sorted(by_path_shape.values(), key=_rank):
        add(state)
    for state in sorted(by_path_shape_score.values(), key=_rank):
        add(state)

    pareto = signals.pareto([(state, _state_signals(state))
                             for state in states])
    for state, _sig in pareto:
        add(state)
    for state in ordered:
        add(state)
    return chosen


def _proposal_tasks(frontier: list[_State], *, pointer_context=None) -> list[tuple[_State, rewrites.Rewrite]]:
    """Round-robin expansions, preferring a new kind on each repair path."""
    per_state: list[list[tuple[_State, rewrites.Rewrite]]] = []
    for state in sorted(frontier, key=_rank):
        proposals = rewrites.propose(state.source, state.attempt.diff)
        if pointer_context:
            from solver import address_units
            report = address_units.parameter_call_views(state.source, **pointer_context)
            if report['changes']:
                original, candidate = state.source, report['source']
                proposals.append(rewrites.Rewrite('target-call byte units', 'address-units',
                    lambda source, old=original, new=candidate: new if source == old else source))
        if "stmtorder" in state.kinds:
            # The allocation-shaped gate chooses this lever once. A swap can
            # make the next residual look transiently structural, so re-gating
            # every step stranded the measured search after one move.
            proposals += rewrites.statement_order_rewrites(
                state.source, state.attempt.diff, gate=False)
        proposals.sort(key=lambda rw: (rw.kind in state.kinds,
                                       rw.kind, rw.label))
        per_state.append([(state, rw) for rw in proposals])

    tasks: list[tuple[_State, rewrites.Rewrite]] = []
    offset = 0
    while True:
        added = False
        for proposals in per_state:
            if offset < len(proposals):
                tasks.append(proposals[offset])
                added = True
        if not added:
            return tasks
        offset += 1


def search(repo: Path, name: str, src: str, ws: Path, conn=None,
           max_pairs: int = 250, verbose: bool = True, *,
           max_depth: int = 6, beam_width: int = 6,
           parent_attempt_id: int | None = None,
           run_id: str = "repair", plateau_config=None,
           baseline_name: str | None = None):
    """Search bounded compositions and return ``(best_attempt, source, log)``.

    ``max_pairs`` is retained for API compatibility; it now caps all rewritten
    candidates rather than only depth-two candidates. The baseline compile is
    not charged to that budget.
    """
    log: list[str] = []
    if conn is not None:
        workspace.configure_compiler(ws, repo, conn, name)
    # A same-job caller can retain the fresh root artifact address so the
    # verified build cache recognizes this exact invocation. score still runs
    # all frontend/exactness gates; no supplied Attempt is trusted here.
    base = workspace.score(ws, repo, baseline_name or name, src)
    if parent_attempt_id is not None:
        # The caller already logged this exact source and verifier result. Use
        # that durable row as the root rather than duplicating it.
        base.receipt_id = parent_attempt_id
    elif conn is not None:
        workspace.record_attempt(
            conn, name, src, base, strategy="repair-baseline",
            run_id=run_id, run_kind="deterministic-repair")
    if not base.compiled:
        return base, src, ["baseline did not compile"]
    if base.exact and plateau_config is None:
        return base, src, ["baseline already EXACT"]
    if max_pairs <= 0 or max_depth <= 0 or beam_width <= 0:
        return base, src, ["repair budget disabled"]

    if plateau_config is not None:
        from solver import plateau
        root = _State(src, base)
        if plateau.verified(root):
            return base, src, ['baseline already verified EXACT']
        if (base.frontend or {}).get('passed') is not True:
            return base, src, ['plateau baseline frontend did not pass']
        def evaluate(candidate, parent, rw):
            return workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                strategy='repair-plateau-v1', run_id=run_id,
                run_kind='deterministic-repair', iteration=len(parent.labels) + 1,
                parent_attempt_id=parent.attempt.receipt_id,
                relation='deterministic-repair', action=rw.label, feedback=parent.attempt.diff)
        best, log = plateau.search(root, evaluate, budget=max_pairs,
                                   max_depth=max_depth, config=plateau_config)
        return best.attempt, best.source, log

    best_att, best_src = base, src
    current = [_State(src, base)]
    seen = {_digest(src)}
    tried = 0
    from solver import frontend_repair
    pointer_context = (dict(function=name, assembly=workspace.target_asm(ws, name), o32=True)
                       if frontend_repair.big_endian_o32(ws/'target.o') else None)

    for depth in range(1, max_depth + 1):
        tasks = _proposal_tasks(current, pointer_context=pointer_context)
        if not tasks:
            log.append(f"depth {depth}: no applicable rewrite")
            break

        compiled: list[_State] = []
        proposed = unique = 0
        for parent, rw in tasks:
            if tried >= max_pairs:
                break
            proposed += 1
            candidate = rw(parent.source)
            key = _digest(candidate)
            if candidate == parent.source or key in seen:
                continue
            seen.add(key)
            unique += 1
            tried += 1
            att = workspace.score(
                ws, repo, name, candidate, conn=conn, func=name,
                strategy=f"repair-d{depth}", run_id=run_id,
                run_kind="deterministic-repair", iteration=depth,
                parent_attempt_id=parent.attempt.receipt_id,
                relation="deterministic-repair", action=rw.label,
                feedback=parent.attempt.diff)
            if not att.compiled:
                continue

            state = _State(candidate, att, parent.labels + (rw.label,),
                           parent.kinds + (rw.kind,))
            compiled.append(state)
            path = " then ".join(state.labels)
            if att.exact:
                log.append(f"EXACT depth {depth}: {path}")
                return att, candidate, log
            if att.score > best_att.score:
                best_att, best_src = att, candidate
                log.append(f"improved depth {depth}: {path} -> {att.score:.3f}")

        # Do not collapse call-site identity before every single rewrite has
        # had one chance to compose. The Linda regression has twelve equal-
        # scoring argument swaps and only one exposes the exact layout repair;
        # a six-state beam cannot distinguish them from the scalar residual.
        frontier_width = max(beam_width, 24) if depth == 1 else beam_width
        current = _frontier(compiled, frontier_width)
        log.append(f"depth {depth}: {proposed} proposed, {unique} unique, "
                   f"{len(compiled)} compiling, {len(current)} kept")
        if verbose:
            for state in sorted(current, key=_rank)[:8]:
                path = " then ".join(state.labels)
                print(f"      d{depth} {path[:38]:38} "
                      f"{state.attempt.score:8.3f}", flush=True)
        if tried >= max_pairs:
            log.append(f"candidate budget exhausted at {tried}")
            break
        if not current:
            break

    log.append(f"{tried} unique rewritten candidates tried")
    return best_att, best_src, log

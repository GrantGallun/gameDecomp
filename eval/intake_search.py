"""Bounded composition of existing intake owners, with a protected incumbent.

Every distinct child is scored through the caller's logging compiler boundary.
Frontend improvements are search hypotheses, never certificates or replacements
for an incumbent that they regress. No KB inference is written here.
"""
from __future__ import annotations

import hashlib
import time

from solver.frontend_diagnostics import errors_are_complete


def _digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def _errors(frontend):
    count = frontend.get('error_count')
    if not errors_are_complete(frontend):
        return float('inf')
    return count


def _quality(node):
    v, f = node['verdict'], node['frontend']
    return (bool(v.get('exact')), bool(v.get('compiled')), f.get('status') == 'passed',
            float(v.get('score') or 0), -_errors(f))


def _preserves(child, parent):
    cv, pv = child['verdict'], parent['verdict']
    return (not (pv.get('exact') and not cv.get('exact'))
            and not (pv.get('compiled') and not cv.get('compiled'))
            and not (parent['frontend'].get('status') == 'passed'
                     and child['frontend'].get('status') != 'passed')
            and _errors(child['frontend']) <= _errors(parent['frontend'])
            and (not pv.get('compiled') or float(cv.get('score') or 0) >= float(pv.get('score') or 0)))


def _beam(nodes, best, width):
    # Protect the public result; reserve alternatives for low frontend error
    # counts and distinct residuals. A bounded archive does not imply convergence.
    from eval.intake_probe import classify_residual
    ranked = sorted(nodes, key=lambda n: (-_errors(n['frontend']), _quality(n), n['index']), reverse=True)
    chosen, signatures = [best], set()
    def signature(node):
        return tuple(sorted({classify_residual(e['what']) for e in node['frontend'].get('errors', [])}))
    signatures.add(signature(best))
    for unique in (True, False):
        for node in ranked:
            if len(chosen) >= width:
                return chosen
            if node in chosen or (unique and signature(node) in signatures):
                continue
            chosen.append(node)
            signatures.add(signature(node))
    return chosen


def search(context, *, runners, sequence, score, observe, max_rounds=3,
           max_attempts=12, beam_width=3, deadline=None):
    """Score(source, parent_hash, action) must log every compiler attempt.

    The supplied initial_verdict belongs to context['candidate']; it has already
    been compiled/logged. Trace records every invoked owner's outcome, including
    unchanged, duplicate, and crashed proposals. nodes retains candidate lineage.
    """
    if max_rounds < 1 or max_attempts < 0 or beam_width < 1:
        raise ValueError('invalid intake search budget')
    source = context['candidate']
    first = dict(source=source, source_sha256=_digest(source), parent=None, action='baseline',
                 verdict=context['initial_verdict'], frontend=observe(source), index=0)
    nodes, best, beam = [first], first, [first]
    seen_sources, invoked, trace = {first['source_sha256']}, set(), []
    attempts, stop = 0, 'round-budget'
    for round_index in range(max_rounds):
        count_before = len(nodes)
        for action in sequence:
            for parent in list(beam):
                if best['verdict'].get('exact'):
                    stop = 'exact'
                    break
                if attempts >= max_attempts:
                    stop = 'attempt-budget'
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    stop = 'time-budget'
                    break
                key = (parent['source_sha256'], action)
                if key in invoked:
                    continue
                invoked.add(key)
                step = dict(round=round_index, action=action, parent=key[0])
                ctx = {**context, 'candidate': parent['source'], 'initial_verdict': parent['verdict'],
                       'diff': parent['verdict'].get('diff') or ''}
                try:
                    proposal = runners[action](ctx, {'allow_partial': True})
                except Exception as exc:
                    step.update(status='crashed', changed=False, reason=f'{type(exc).__name__}: {exc}')
                    trace.append(step)
                    continue
                step.update(status=proposal.get('status'), changed=bool(proposal.get('changed')),
                            reason=proposal.get('reason') or '', detail=proposal.get('detail'))
                trace.append(step)
                child_source = proposal.get('source')
                if not step['changed'] or not isinstance(child_source, str) or not child_source.strip():
                    continue
                digest = _digest(child_source)
                step['child'] = digest
                if digest in seen_sources:
                    step['duplicate'] = True
                    continue
                # Fail closed on compiler/observer errors; don't report a short
                # or unlogged replay as a successful search.
                verdict = score(child_source, key[0], action)
                attempts += 1
                child = dict(source=child_source, source_sha256=digest, parent=key[0], action=action,
                             verdict=verdict, frontend=observe(child_source), index=len(nodes))
                nodes.append(child)
                seen_sources.add(digest)
                step['compiled'] = bool(verdict.get('compiled'))
                step['exact'] = bool(verdict.get('exact'))
                step['adopted'] = _preserves(child, best) and _quality(child) > _quality(best)
                if step['adopted']:
                    best = child
                beam = _beam(nodes, best, beam_width)
            if stop != 'round-budget':
                break
        if stop != 'round-budget':
            break
        if best['verdict'].get('exact'):
            stop = 'exact'
            break
        if len(nodes) == count_before:
            stop = 'fixed-point'
            break
    return dict(source=best['source'], verdict=best['verdict'], frontend=best['frontend'],
                source_sha256=best['source_sha256'], nodes=nodes, trace=trace,
                attempts=attempts, stop_reason=stop)

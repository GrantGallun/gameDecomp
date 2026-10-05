"""Bounded, logged plateau search (solver/plateau_search) on a retained campaign candidate.

Scheduled after the node's site-edit visit for the current site-edit revision (solver.repair_queue.plateau_profile).
Measured on the 219 near misses (within 10 instructions) the site-edit search left
(eval/results/loop-shape-20260930): 5 exact in 47,345 compiles, 3 of them register-only functions where register
search had failed. Every child is a hypothesis; workspace.score, the frontend gate and the certificates decide.
Promotion is the site-edit route's rule: exact, or better on gradient without a lower similarity score.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from eval.site_edit_repair import _hash, _retained_source
from solver import plateau_search, residual, site_edits, workspace

BUDGET = 240


def run(*, repo: Path, db: Path, function: str, node: dict, out: Path, budget: int = BUDGET) -> dict:
    repo, db, out = Path(repo), Path(db), Path(out)
    if node.get('status') != 'pending' or (node.get('residual') or {}).get('compiled') is not True \
            or ((node.get('residual') or {}).get('frontend') or {}).get('passed') is not True:
        raise ValueError('plateau repair requires a pending compiled frontend-valid incumbent')
    budget = max(0, min(int(budget), BUDGET))
    out.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=300)
    try:
        source = _retained_source(conn, function, node)
        ws = workspace.bootstrap(repo, function)
        run_id = out.stem
        by_source: dict[str, tuple] = {}
        log: list[dict] = []

        def score(code: str, label: str, parent_code: str | None):
            parent_id = (node['attempt_id'] if parent_code is None
                         else by_source[_hash(parent_code)][0].receipt_id)
            tag = f'{function}_plateau_{time.time_ns()}'
            attempt = workspace.score(ws, repo, tag, code, conn=conn, func=function,
                                      strategy=f'plateau:{label}'[:120], run_id=run_id,
                                      run_kind='plateau', parent_attempt_id=parent_id,
                                      relation='plateau', action=label[:120])
            conn.commit()
            by_source[_hash(code)] = (attempt, ws / f'{tag}.o')
            log.append({'label': label, 'receipt_id': attempt.receipt_id, 'compiled': attempt.compiled,
                        'score': attempt.score, 'gradient': list(site_edits.gradient(attempt)),
                        'complete': workspace.repair_complete(attempt)})
            return attempt

        result = plateau_search.search(score, source, function, budget=budget)
        baseline, _ = by_source[_hash(source)]
        chosen = None
        if result['exact']:
            chosen = result['source']
        else:
            best_attempt, _ = by_source.get(_hash(result['source']), (None, None))
            if (best_attempt is not None and result['source'] != source
                    and site_edits.gradient(best_attempt) < site_edits.gradient(baseline)
                    and (best_attempt.score or 0) >= (node.get('score') or 0)):
                chosen = result['source']
        if chosen is not None:
            attempt, obj = by_source[_hash(chosen)]
            path = out.with_suffix('.best.c')
            path.write_text(chosen)
            packet = residual.build(attempt, target_asm=workspace.target_asm(ws, function),
                                    target_object=ws / 'target.o', candidate_object=obj).to_dict()
            payload = {'status': 'evaluated', 'source': str(path), 'source_sha256': _hash(chosen),
                       'attempt_id': attempt.receipt_id, 'score': attempt.score, 'residual': packet,
                       'verification': attempt.verification, 'exact': workspace.repair_complete(attempt)}
        else:
            payload = {key: node.get(key) for key in
                       ('source', 'source_sha256', 'attempt_id', 'score', 'residual', 'verification')}
            payload.update(status='evaluated', exact=False)
        semantic = node.get('semantic_validation') or {}
        payload['semantic_validation'] = (semantic if payload['source_sha256'] ==
                                          node['source_sha256'] == semantic.get('source_sha256') else None)
        payload['best_score_improved'] = bool(chosen is not None and (payload['score'] or 0) > (node.get('score') or 0))
        payload['proposal_compiles'] = result['compiles']
        payload['distances'] = {'baseline': result.get('baseline_gradient'), 'best': result.get('best_gradient')}
        trail = out.with_suffix('.trail.json')
        trail.write_text(json.dumps(result['trail'], indent=1))
        payload['log'] = log[-3:] + [{'trail': str(trail), 'compiles': result['compiles']}]
        return payload
    finally:
        conn.close()

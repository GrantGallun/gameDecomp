"""Bounded, logged localized-typed-edit repair of a retained campaign candidate (solver/site_edits).

Measured on the 77-function small near-miss frame (eval/results/site-edits-20260929): 13 byte-exact in
1,200 compiles, where the shape-specific rewrite pool had proposed nothing for 60 of them. Every child
is a hypothesis: `workspace.score`, the frontend gate and the object-section certificate decide.

Promotion is conservative: an exact child, or a child that is better on `site_edits.gradient`
(instruction distance, then register distance) WITHOUT a lower similarity score. The score still
ranks other campaign routes, so a gradient-only improvement that lowers it is logged, not promoted.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from solver import residual, site_edits, workspace


def _hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _retained_source(conn: sqlite3.Connection, function: str, node: dict) -> str:
    source = Path(node['source']).read_text()
    if _hash(source) != node['source_sha256']:
        raise ValueError('retained source hash differs from campaign node')
    row = conn.execute(
        'SELECT f.name,a.source_code,a.source_sha256 FROM attempts a '
        'JOIN functions f ON f.addr=a.func_addr WHERE a.id=?', (node['attempt_id'],)).fetchone()
    if row is None or row[0] != function or row[1] != source or row[2] != _hash(source):
        raise ValueError('retained source hash differs from durable attempt')
    return source


def run(*, repo: Path, db: Path, function: str, node: dict, out: Path, budget: int = 72) -> dict:
    """One site-edit visit; the baseline and every child receive durable attempts with parent edges."""
    repo, db, out = Path(repo), Path(db), Path(out)
    if node.get('status') != 'pending' or (node.get('residual') or {}).get('compiled') is not True \
            or ((node.get('residual') or {}).get('frontend') or {}).get('passed') is not True:
        raise ValueError('site-edit repair requires a pending compiled frontend-valid incumbent')
    budget = max(0, min(int(budget), 72))   # depth 3 x per_step 24; at 48 the third level was unreachable
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
            tag = f'{function}_siteedit_{time.time_ns()}'
            attempt = workspace.score(ws, repo, tag, code, conn=conn, func=function,
                                      strategy=f'site-edits:{label}'[:120], run_id=run_id,
                                      run_kind='site-edits', parent_attempt_id=parent_id,
                                      relation='site-edits', action=label[:120])
            conn.commit()
            by_source[_hash(code)] = (attempt, ws / f'{tag}.o')
            log.append({'label': label, 'receipt_id': attempt.receipt_id, 'compiled': attempt.compiled,
                        'score': attempt.score, 'gradient': list(site_edits.gradient(attempt)),
                        'complete': workspace.repair_complete(attempt)})
            return attempt

        result = site_edits.search(score, source, function, budget=budget)
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

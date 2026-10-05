"""Bounded, logged operand repair of a retained campaign candidate.

This route reuses the measured binary-evidence mutation stream. A proposal is
only a hypothesis: workspace.score, the frontend gate, and the object-section
certificate determine whether it may replace the incumbent.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from solver import diffrepair, file_scope_objects, regalloc_mutations, relocation_names, residual, rodata_symbol, workspace


def _hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _usable(attempt: workspace.Attempt) -> bool:
    return bool(attempt.compiled and (attempt.frontend or {}).get('passed') is True)


def _rank(attempt: workspace.Attempt) -> tuple[bool, float]:
    return workspace.repair_complete(attempt), attempt.score or 0.0


def _proposals(source: str, function: str, attempt: workspace.Attempt, *, repo: Path,
               ws: Path, map_text: str, log: list[dict], candidate_obj: Path | None = None):
    """Original operand-repair order, with the complete fresh compiler verdict."""
    out = []
    try:
        for label, candidate in rodata_symbol.variants(
                source, function, attempt.diff or '', attempt.source_attribution,
                elf=repo / 'build/snowboardkids.elf', map_text=map_text,
                target_obj=ws / 'target.o'):
            out.append((label, 'rodata_symbol', candidate))
    except Exception as exc:
        log.append({'kind': 'rodata_symbol', 'status': 'declined', 'reason': repr(exc)[:300]})
    # Address-taken rodata reads the two objects, not the diff: the normalized diff hides it
    # (eval/results/hidden-object-20260930).
    if candidate_obj is not None:
        try:
            for label, candidate in rodata_symbol.address_variants(
                    source, function, target_obj=ws / 'target.o', candidate_obj=candidate_obj):
                out.append((label, 'rodata_address', candidate))
        except Exception as exc:
            log.append({'kind': 'rodata_address', 'status': 'declined', 'reason': repr(exc)[:300]})
        try:
            for label, candidate in file_scope_objects.variants(
                    source, function, target_obj=ws / 'target.o', candidate_obj=candidate_obj):
                out.append((label, 'unused_file_scope_object', candidate))
        except Exception as exc:
            log.append({'kind': 'unused_file_scope_object', 'status': 'declined', 'reason': repr(exc)[:300]})
    # Relocation-name repairs read from the object diff: B+K -> named global, struct field -> named global. The module
    # existed since 2026-09-14 but nothing in the pipeline called it. `literal_names` is left out on purpose: it swaps a
    # literal for `extern D_800E...`, which scores higher but cannot link (measured 2026-09-30: fadeInRaceGameplayViewports
    # went 99.52 -> 100 function-exact and then failed the whole-ROM build with an undefined reference).
    try:
        for label, kind, candidate in relocation_names.variants(source, function, attempt.diff or ''):
            if not label.startswith('literal_names'):
                out.append((label, kind, candidate))
    except Exception as exc:
        log.append({'kind': 'relocation_name', 'status': 'declined', 'reason': repr(exc)[:300]})
    rodata_count = len(out)
    try:
        evidence = {'source_attribution': attempt.source_attribution,
                    'frontend': attempt.frontend,
                    'compiler_recipe': attempt.compiler_recipe,
                    'verification': attempt.verification}
        out.extend(regalloc_mutations.variants(source, function, attempt.diff or '',
                                               evidence=evidence))
    except Exception as exc:
        log.append({'kind': 'regalloc_mutations', 'status': 'declined', 'reason': repr(exc)[:300]})
    try:
        code, changed, _ = diffrepair.repair(source, attempt.diff or '')
        if changed and code != source:
            out.insert(rodata_count, ('diffrepair', 'diffrepair', code))
    except Exception as exc:
        log.append({'kind': 'diffrepair', 'status': 'declined', 'reason': repr(exc)[:300]})
    return out


def _retained_source(conn: sqlite3.Connection, function: str, node: dict) -> str:
    source = Path(node['source']).read_text()
    source_hash = _hash(source)
    if source_hash != node['source_sha256']:
        raise ValueError('retained source hash differs from campaign node')
    row = conn.execute(
        'SELECT f.name,a.source_code,a.source_sha256 FROM attempts a '
        'JOIN functions f ON f.addr=a.func_addr WHERE a.id=?',
        (node['attempt_id'],)).fetchone()
    if row is None or row[0] != function or row[1] != source or row[2] != source_hash:
        raise ValueError('retained source hash differs from durable attempt')
    return source


def run(*, repo: Path, db: Path, function: str, node: dict, out: Path,
        budget: int = 72) -> dict:
    """Try 40 + 16 + 16 proposals, eight per step; return a campaign result.

    The baseline and every child, including failures and rejected exact C,
    receive durable attempts and true parent edges in the worker database.
    Existing frontier rows remain with the controller: this result omits the
    `frontier` field rather than overwriting it with a one-path search.
    """
    repo, db, out = Path(repo), Path(db), Path(out)
    if node.get('status') != 'pending' or (node.get('residual') or {}).get('compiled') is not True \
            or ((node.get('residual') or {}).get('frontend') or {}).get('passed') is not True:
        raise ValueError('operand repair requires a pending compiled frontend-valid incumbent')
    if budget < 0:
        raise ValueError('negative operand repair budget')
    budget = min(int(budget), 72)
    out.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=300)
    try:
        source = _retained_source(conn, function, node)
        ws = workspace.bootstrap(repo, function)
        map_path = repo / 'build/snowboardkids.map'
        map_text = map_path.read_text(errors='replace') if map_path.is_file() else ''
        log: list[dict] = []
        seen = {source}
        proposal_count = 0
        run_id = out.stem

        def score(candidate: str, parent_id: int, kind: str, label: str):
            nonlocal proposal_count
            iteration = 0 if kind == 'baseline' else proposal_count + 1
            tag = f'{function}_operand_{time.time_ns()}_{iteration}'
            attempt = workspace.score(
                ws, repo, tag, candidate, conn=conn, func=function,
                strategy=f'operand-repair:{kind}:{label}'[:120],
                run_id=run_id, run_kind='operand-repair', iteration=iteration,
                parent_attempt_id=parent_id, relation='operand-repair',
                action=f'{kind}:{label}'[:120])
            conn.commit()
            if kind != 'baseline':
                proposal_count += 1
            log.append({'kind': kind, 'label': label, 'receipt_id': attempt.receipt_id,
                        'parent_attempt_id': parent_id, 'source_sha256': _hash(candidate),
                        'compiled': attempt.compiled, 'score': attempt.score,
                        'exact': attempt.exact, 'frontend_passed':
                        (attempt.frontend or {}).get('passed'),
                        'complete': workspace.repair_complete(attempt)})
            return attempt, ws / f'{tag}.o'

        baseline, baseline_obj = score(source, node['attempt_id'], 'baseline', 'retained')
        selected_source, selected_attempt, selected_obj = source, None, None
        if _usable(baseline) and (_rank(baseline) > (False, node.get('score') or 0.0)):
            selected_attempt, selected_obj = baseline, baseline_obj
        best_source, best_attempt, best_obj = source, baseline, baseline_obj
        reproduced = _usable(baseline) and abs(baseline.score - (node.get('score') or 0.0)) <= 0.01
        if reproduced and not workspace.repair_complete(baseline):
            for stage_budget in (40, 16, 16):
                stage_limit = min(stage_budget, budget - proposal_count)
                stage_used = 0
                while stage_used < stage_limit and not workspace.repair_complete(best_attempt):
                    children = []
                    in_batch = set()
                    for label, kind, candidate in _proposals(
                            best_source, function, best_attempt, repo=repo, ws=ws,
                            map_text=map_text, log=log, candidate_obj=best_obj):
                        if candidate in seen or candidate in in_batch:
                            continue
                        in_batch.add(candidate)
                        children.append((label, kind, candidate))
                        if len(children) >= 8:
                            break
                    if not children:
                        break
                    step = None
                    for label, kind, candidate in children:
                        if stage_used >= stage_limit:
                            break
                        seen.add(candidate)
                        attempt, object_path = score(candidate, best_attempt.receipt_id, kind, label)
                        stage_used += 1
                        if not _usable(attempt):
                            continue
                        if workspace.repair_complete(attempt):
                            best_source, best_attempt, best_obj = candidate, attempt, object_path
                            selected_source, selected_attempt, selected_obj = candidate, attempt, object_path
                            break
                        if step is None or _rank(attempt) > _rank(step[1]):
                            step = (candidate, attempt, object_path)
                    if workspace.repair_complete(best_attempt):
                        break
                    if step is None or _rank(step[1]) <= _rank(best_attempt):
                        break
                    best_source, best_attempt, best_obj = step
                    if _rank(best_attempt) > (False, node.get('score') or 0.0):
                        selected_source, selected_attempt, selected_obj = best_source, best_attempt, best_obj
                if workspace.repair_complete(best_attempt) or proposal_count >= budget:
                    break
        elif not reproduced:
            log.append({'kind': 'baseline', 'status': 'declined',
                        'reason': 'compiler baseline did not reproduce retained score and frontend'})

        promoted = selected_attempt is not None
        if promoted:
            source_path = out.with_suffix('.best.c')
            source_path.write_text(selected_source)
            packet = residual.build(selected_attempt, target_asm=workspace.target_asm(ws, function),
                                    target_object=ws / 'target.o', candidate_object=selected_obj).to_dict()
            result = {'status': 'evaluated', 'source': str(source_path),
                      'source_sha256': _hash(selected_source),
                      'attempt_id': selected_attempt.receipt_id,
                      'score': selected_attempt.score,
                      'residual': packet, 'verification': selected_attempt.verification,
                      'exact': workspace.repair_complete(selected_attempt)}
        else:
            result = {key: node.get(key) for key in
                      ('source', 'source_sha256', 'attempt_id', 'score', 'residual', 'verification')}
            result.update(status='evaluated', exact=False)
        semantic = node.get('semantic_validation') or {}
        result['semantic_validation'] = (semantic if result['source_sha256'] ==
                                         node['source_sha256'] == semantic.get('source_sha256') else None)
        result['best_score_improved'] = bool(promoted and result['score'] > (node.get('score') or 0.0))
        result['proposal_compiles'] = proposal_count
        result['log'] = log
        return result
    finally:
        conn.close()

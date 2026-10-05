"""Private two-node fast-controller canary of the frozen operand route.

No historical winner or reference C is loaded. The only candidate roots are
the hash-verified incumbent and retained frontier attempts in the live native
checkpoint. All controller writes go to --out under the private WSL workspace.
"""
from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

NAMES = ('audioThreadMain', 'releaseSoundEffectHandleNode')


def copy_rows(source, target, table, where='', params=()):
    columns = [row[1] for row in source.execute(f'PRAGMA table_info({table})')]
    marks = ','.join('?' for _ in columns)
    rows = list(source.execute(f'SELECT * FROM {table} {where}', params))
    if rows:
        target.executemany(f'INSERT INTO {table} VALUES ({marks})', rows)
    return len(rows)


def run(live_state: Path, out: Path, project: Path, report: Path, workers: int = 1):
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    sys.path.insert(0, str(project))
    from eval import campaign_state, completion_campaign as campaign, fast_campaign
    from solver import repair_queue

    live = campaign_state.read(live_state)
    source_checkpoint = json.loads(live_state.read_text()).get('commit')
    repo = Path(live['config']['repo'])
    source_db = Path(live['config']['db'])
    nodes = {name: copy.deepcopy(live['nodes'][name]) for name in NAMES}
    revision = repair_queue.binary_input_revision(live)
    initial_profiles = {name: repair_queue.next_profile(
        nodes[name], live['config']['model_calls'], campaign.PROFILES, revision)
        for name in NAMES}
    retained = {name: campaign.retained_candidates(nodes[name]) for name in NAMES}
    addresses = tuple(node['address'] for node in nodes.values())
    ids = {int(node['attempt_id']) for node in nodes.values()}
    ids.update(int(item['attempt_id']) for alternatives in retained.values() for item in alternatives)
    private_db = out / 'private.sqlite'

    with sqlite3.connect(f'file:{source_db}?mode=ro', uri=True) as source:
        source.execute('BEGIN')
        # Include each retained attempt's actual parent chain, not arbitrary
        # campaign attempts or any private research candidate.
        needed = set()
        todo = list(ids)
        while todo:
            attempt_id = todo.pop()
            if attempt_id in needed:
                continue
            row = source.execute('SELECT parent_attempt_id,func_addr FROM attempts WHERE id=?',
                                 (attempt_id,)).fetchone()
            if row is None or row[1] not in addresses:
                raise ValueError(f'missing or foreign retained attempt {attempt_id}')
            needed.add(attempt_id)
            if row[0] is not None:
                todo.append(int(row[0]))
        with sqlite3.connect(private_db) as target:
            target.executescript((project / 'kb/schema.sql').read_text())
            target.execute('PRAGMA foreign_keys=OFF')
            func_marks = ','.join('?' for _ in addresses)
            counts = {}
            tu_ids = tuple(row[0] for row in source.execute(
                f'SELECT DISTINCT tu_id FROM functions WHERE addr IN ({func_marks}) AND tu_id IS NOT NULL', addresses))
            if tu_ids:
                marks = ','.join('?' for _ in tu_ids)
                counts['tus'] = copy_rows(source, target, 'tus', f'WHERE id IN ({marks})', tu_ids)
            else:
                counts['tus'] = 0
            counts['functions'] = copy_rows(source, target, 'functions',
                                           f'WHERE addr IN ({func_marks})', addresses)
            extraction_ids = tuple(row[0] for row in source.execute(
                f'SELECT DISTINCT extraction_id FROM evidence WHERE func_addr IN ({func_marks})', addresses))
            if extraction_ids:
                marks = ','.join('?' for _ in extraction_ids)
                counts['extraction'] = copy_rows(source, target, 'extraction',
                                                 f'WHERE id IN ({marks})', extraction_ids)
            else:
                counts['extraction'] = 0
            counts['evidence'] = copy_rows(source, target, 'evidence',
                                           f'WHERE func_addr IN ({func_marks})', addresses)
            ordered_ids = tuple(sorted(needed))
            marks = ','.join('?' for _ in ordered_ids)
            run_ids = tuple(row[0] for row in source.execute(
                f'SELECT DISTINCT run_id FROM attempts WHERE id IN ({marks}) AND run_id IS NOT NULL', ordered_ids))
            if run_ids:
                run_marks = ','.join('?' for _ in run_ids)
                counts['attempt_runs'] = copy_rows(source, target, 'attempt_runs',
                                                   f'WHERE id IN ({run_marks})', run_ids)
            else:
                counts['attempt_runs'] = 0
            counts['attempts'] = copy_rows(source, target, 'attempts',
                                           f'WHERE id IN ({marks})', ordered_ids)
            counts['attempt_edges'] = copy_rows(source, target, 'attempt_edges',
                f'WHERE parent_attempt_id IN ({marks}) AND child_attempt_id IN ({marks})',
                ordered_ids + ordered_ids)
            target.execute('PRAGMA foreign_keys=ON')
            fk = target.execute('PRAGMA foreign_key_check').fetchall()
            if fk:
                raise ValueError(f'private DB foreign-key failures: {fk[:5]}')
            target.commit()
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('private DB quick_check failed')

    for name, node in nodes.items():
        original = Path(node['source']).read_text()
        digest = hashlib.sha256(original.encode()).hexdigest()
        if digest != node['source_sha256']:
            raise ValueError(f'{name} incumbent path hash mismatch')
        row = sqlite3.connect(f'file:{private_db}?mode=ro', uri=True).execute(
            'SELECT source_code,source_sha256 FROM attempts WHERE id=?',
            (node['attempt_id'],)).fetchone()
        if row != (original, digest):
            raise ValueError(f'{name} durable incumbent hash mismatch')
        source_path = out / 'retained' / f'{name}.c'
        source_path.parent.mkdir(exist_ok=True)
        source_path.write_text(original)
        node['source'] = str(source_path)

    private = dict(live)
    private['nodes'] = nodes
    private['config'] = {**live['config'], 'db': str(private_db), 'project': str(project)}
    private['pins'] = campaign._pins(project, repo)
    private['pins'].update(campaign.frozen_wavefront.file_hashes(
        [Path(path) for path in live['pins'] if Path(path).is_relative_to(repo / 'nonmatchings')]))
    private['inventory_sha256'] = campaign.digest(list(sqlite3.connect(private_db).execute(
        'SELECT name,addr,size,insn_count FROM functions ORDER BY name')))
    private['inventory_count'] = len(nodes)
    private.pop('repair_queue', None)
    private.pop('binary_data_catalog', None)
    private.pop('binary_data_memory', None)
    private.pop('runtime_options', None)
    private['fast_inflight'] = []
    private['inflight'] = None
    private['fast_metrics'] = {}
    del live
    gc.collect()
    state_path = out / 'state.json'
    campaign_state.Store(state_path).save(private, changed=NAMES)
    (out / 'state-artifacts').mkdir()
    queue, _ = repair_queue.project(private, campaign.PROFILES)
    cloned_profiles = {name: queue['work_items'].get(name, {}).get('profile') for name in NAMES}
    if any(cloned_profiles[name] != (initial_profiles[name] or {}).get('name') for name in NAMES):
        raise ValueError(f'private scheduler eligibility changed: {cloned_profiles}')
    if any(initial_profiles[name] is None or initial_profiles[name].get('model') for name in NAMES):
        raise ValueError('one of the two retained nodes lacks deterministic work')

    cfg = private['config']
    args = SimpleNamespace(repo=repo, db=private_db, project=project, state=state_path,
                           worker_root=out / 'workers', resume=True, deterministic_only=True,
                           scheduler='evidence-v1', workers=workers, dispatch='wave', model_parallel=1,
                           model_workers=1, tasks_per_worker=1, reasoned_effort='profile',
                           max_work_items=8, model_calls=cfg['model_calls'], model=cfg['model'],
                           endpoint=cfg['endpoint'], timeout=cfg['timeout'],
                           num_predict=cfg['num_predict'], integrate=False, runtime_plan=None)
    error = None
    try:
        with (out / 'fast-controller.log').open('w') as log:
            from contextlib import redirect_stdout
            with redirect_stdout(log):
                finished = fast_campaign.run(args)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        finished = campaign_state.read(state_path)
    imported = {}
    with sqlite3.connect(f'file:{private_db}?mode=ro', uri=True) as conn:
        imported['attempts_after'] = conn.execute('SELECT count(*) FROM attempts').fetchone()[0]
        imported['attempt_edges_after'] = conn.execute('SELECT count(*) FROM attempt_edges').fetchone()[0]
        imported['new_attempts'] = imported['attempts_after'] - counts['attempts']
        imported['new_edges'] = imported['attempt_edges_after'] - counts['attempt_edges']
        imported['foreign_key_failures'] = conn.execute('PRAGMA foreign_key_check').fetchall()[:8]
        imported['new_model_attempts'] = conn.execute(
            'SELECT count(*) FROM attempts WHERE id>? AND model NOT IN ("", "zero-model")',
            (max(needed),)).fetchone()[0]
    results = {}
    for name in NAMES:
        before = nodes[name]
        after = finished['nodes'][name]
        new_jobs = after.get('jobs', [])[len(before.get('jobs', [])):]
        receipts = []
        for job in new_jobs:
            path = Path(job['receipt'])
            raw = json.loads(path.read_text()) if path.exists() else {}
            edits = [row for row in raw.get('log', []) if row.get('kind') == 'local_web_merge']
            receipts.append({'profile': job['profile'], 'receipt': str(path),
                             'status': raw.get('status'), 'exact': raw.get('exact'),
                             'attempt_id': raw.get('attempt_id'),
                             'private_lineage': raw.get('private_lineage'),
                             'proposal_compiles': raw.get('proposal_compiles'),
                             'local_web_merge': edits})
        results[name] = {
            'before': {key: before.get(key) for key in ('status', 'score', 'attempt_id', 'source_sha256')},
            'after': {key: after.get(key) for key in ('status', 'score', 'attempt_id', 'source_sha256')},
            'initial_profile': (initial_profiles[name] or {}).get('name'),
            'new_jobs': receipts,
            'prior_job_history_preserved': after.get('jobs', [])[:len(before.get('jobs', []))] == before.get('jobs', []),
            'frontend_passed': ((after.get('residual') or {}).get('frontend') or {}).get('passed'),
            'certificate_status': (after.get('verification') or {}).get('status'),
        }
    summary = {'source_checkpoint': source_checkpoint, 'project': str(project),
               'private_state': str(state_path), 'private_db': str(private_db),
               'copied_rows': counts, 'imported': imported,
               'last_session': (finished.get('fast_metrics') or {}).get('last_session'),
               'fast_inflight': len(finished.get('fast_inflight') or []),
               'results': results, 'error': error}
    report.write_text(json.dumps(summary, indent=2, default=str) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    summary = run(args.state, args.out, args.project, args.report, args.workers)
    print(json.dumps({'results': {name: {'initial_profile': row['initial_profile'],
                                       'after': row['after'],
                                       'profiles': [j['profile'] for j in row['new_jobs']]}
                                  for name, row in summary['results'].items()},
                      'imported': summary['imported'], 'error': summary['error']}, indent=2))
    return 0 if summary['error'] is None else 1


if __name__ == '__main__':
    raise SystemExit(main())

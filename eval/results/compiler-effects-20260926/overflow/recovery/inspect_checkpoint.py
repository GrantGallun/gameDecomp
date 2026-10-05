"""Read-only inventory of the paused campaign's unresolved fast jobs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

CONTROL = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
FROZEN = CONTROL / 'code'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exists_hash(path: Path) -> dict:
    return {'exists': path.is_file(), 'sha256': sha(path) if path.is_file() else None}


def rows_after(path: Path, cutoffs: dict) -> dict:
    if not path.is_file():
        return {'exists': False}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        return {
            'exists': True,
            'attempts_after_cutoff': conn.execute(
                'SELECT COUNT(*) FROM attempts WHERE id>?', (cutoffs['attempts'],)).fetchone()[0],
            'attempt_ids_after_cutoff': [r[0] for r in conn.execute(
                'SELECT id FROM attempts WHERE id>? ORDER BY id', (cutoffs['attempts'],))],
            'proposals_after_cutoff': conn.execute(
                'SELECT COUNT(*) FROM model_proposals WHERE id>?',
                (cutoffs['model_proposals'],)).fetchone()[0],
        }


def main() -> None:
    before = STATE.read_bytes()
    pointer = json.loads(before)
    with sqlite3.connect((NATIVE / pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
            raise RuntimeError('checkpoint manifest missing or corrupt')
        manifest = json.loads(row[0])

        def object_for(key: str) -> dict:
            row = conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()
            if row is None:
                raise RuntimeError(f'checkpoint object missing: {key}')
            payload = zlib.decompress(row[0])
            if hashlib.sha256(payload).hexdigest() != key:
                raise RuntimeError(f'checkpoint object corrupt: {key}')
            return json.loads(payload)

        state = object_for(manifest['metadata'])
        state['nodes'] = {job['function']: object_for(manifest['nodes'][job['function']])
                          for job in state.get('fast_inflight', [])}
    with sqlite3.connect((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        imported = {}
        for job in state.get('fast_inflight', []):
            row = conn.execute('SELECT mapping FROM campaign_worker_imports WHERE job_id=?',
                               (job['id'],)).fetchone()
            if row:
                imported[job['id']] = json.loads(row[0])
    jobs = []
    for job in state.get('fast_inflight', []):
        raw_path = Path(job['raw'])
        raw = json.loads(raw_path.read_bytes()) if raw_path.is_file() else None
        jobs.append({
            'id': job['id'], 'function': job['function'],
            'profile': job['profile']['name'], 'model': job['profile'].get('model'),
            'slot': job['slot'], 'job_pin_sha256': job['pin_sha256'],
            'node_source_sha256': state['nodes'][job['function']]['source_sha256'],
            'node_source_file': exists_hash(Path(state['nodes'][job['function']]['source'])),
            'node_status': state['nodes'][job['function']]['status'],
            'accepted_jobs': len(state['nodes'][job['function']]['jobs']),
            'dispatch_source_sha256': job['node']['source_sha256'],
            'evidence_key': job['profile'].get('evidence_key'),
            'raw': {'path': job['raw'], **exists_hash(raw_path),
                    'status': raw.get('status') if raw else None,
                    'source_sha256': raw.get('source_sha256') if raw else None,
                    'source_file': exists_hash(Path(raw['source'])) if raw and raw.get('source') else None,
                    'wall_seconds': raw.get('wall_seconds') if raw else None,
                    'model_calls': raw.get('performance', {}).get('model_calls') if raw else None},
            'receipt': {'path': job['receipt'], **exists_hash(Path(job['receipt']))},
            'private_database': rows_after(Path(job['db']), job['cutoffs']),
            'already_imported': job['id'] in imported,
            'imported_attempts': len(imported[job['id']]['attempts']) if job['id'] in imported else None,
        })
    if STATE.read_bytes() != before:
        raise RuntimeError('checkpoint pointer changed during inspection')
    print(json.dumps({
        'commit': pointer['commit'], 'pointer_sha256': hashlib.sha256(before).hexdigest(),
        'summary': pointer['summary'], 'status': pointer['status'],
        'pause_control': (CONTROL / 'service.pause').exists(),
        'pause_native': (NATIVE / 'service.pause').exists(),
        'frozen_solver_sha256': sha(FROZEN / 'solver/mips_differential.py'),
        'pinned_solver_sha256': state['pins'].get(str(FROZEN / 'solver/mips_differential.py')),
        'pin_count': len(state['pins']),
        'pin_digest': hashlib.sha256(json.dumps(state['pins'], sort_keys=True).encode()).hexdigest(),
        'jobs': jobs,
    }, indent=2))


if __name__ == '__main__':
    main()

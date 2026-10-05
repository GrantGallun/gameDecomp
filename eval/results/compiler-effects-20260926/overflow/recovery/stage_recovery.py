"""Archive only the three paused jobs' private deltas; never write live state."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import zlib

from inspect_checkpoint import CONTROL, FROZEN, NATIVE, STATE, sha

HERE = Path(__file__).resolve().parent
STAGE = HERE / 'stage-29289'
EXPECTED_POINTER = 'bbbf44a4707255531e21798ec5d80f676d0acae93f7b55456ee84893d9694d85'
EXPECTED_PIN = '43829448756f0c4fcd677982bcd02651c7332d54e515a55dfe775fb48e700296'
EXPECTED_OLD_SOLVER = '61ca9a6552b3e13fb64f72a9f19a66aee294d5ed94092c08e9966f6353659f66'
EXPECTED_JOBS = {
    '1790461381633671072-initAudioSynthesizer': (False, 5),
    '1790461389096471499-drawRaceSetupSaveChoicePrompts': (True, 5),
    '1790461392479571347-updateRaceScoreAttackRings': (True, 6),
}


def encode(value):
    if isinstance(value, bytes):
        return {'__base64__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [encode(item) for item in value]
    return value


def object_for(conn, key):
    row = conn.execute('SELECT payload FROM objects WHERE hash=?', (key,)).fetchone()
    if row is None:
        raise RuntimeError(f'missing state object {key}')
    raw = zlib.decompress(row[0])
    if hashlib.sha256(raw).hexdigest() != key:
        raise RuntimeError(f'corrupt state object {key}')
    return json.loads(raw)


def delta(job):
    path = Path(job['db'])
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cutoff = job['cutoffs']
        attempts = [dict(r) for r in conn.execute(
            'SELECT * FROM attempts WHERE id>? ORDER BY id', (cutoff['attempts'],))]
        proposals = [dict(r) for r in conn.execute(
            'SELECT * FROM model_proposals WHERE id>? ORDER BY id',
            (cutoff['model_proposals'],))]
        edges = [dict(r) for r in conn.execute(
            'SELECT * FROM attempt_edges WHERE child_attempt_id>? ORDER BY child_attempt_id',
            (cutoff['attempts'],))]
        run_ids = sorted({r['run_id'] for r in attempts + proposals if r.get('run_id')})
        runs = []
        for run_id in run_ids:
            row = conn.execute('SELECT * FROM attempt_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise RuntimeError(f'missing private run {run_id} for {job["id"]}')
            runs.append(dict(row))
        return encode({'job_id': job['id'], 'private_db': str(path),
                       'cutoffs': cutoff, 'attempts': attempts,
                       'model_proposals': proposals, 'attempt_edges': edges,
                       'attempt_runs': runs})


def main():
    if STAGE.exists():
        raise RuntimeError(f'stage already exists: {STAGE}')
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both pause markers required')
    pointer_bytes = STATE.read_bytes()
    if hashlib.sha256(pointer_bytes).hexdigest() != EXPECTED_POINTER:
        raise RuntimeError('checkpoint pointer moved')
    pointer = json.loads(pointer_bytes)
    if pointer.get('commit') != 29289:
        raise RuntimeError('unexpected checkpoint commit')
    with sqlite3.connect((NATIVE / pointer['store']).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
            raise RuntimeError('corrupt checkpoint manifest')
        state_manifest = json.loads(row[0])
        state = object_for(conn, state_manifest['metadata'])
    jobs = state.get('fast_inflight', [])
    if {j['id'] for j in jobs} != set(EXPECTED_JOBS) or state.get('inflight'):
        raise RuntimeError('unexpected in-flight jobs')
    if len(state['pins']) != 3314 or hashlib.sha256(
            json.dumps(state['pins'], sort_keys=True).encode()).hexdigest() != EXPECTED_PIN:
        raise RuntimeError('old pin set changed')
    target = FROZEN / 'solver/mips_differential.py'
    if sha(target) != EXPECTED_OLD_SOLVER or state['pins'].get(str(target)) != EXPECTED_OLD_SOLVER:
        raise RuntimeError('old frozen solver changed')
    for job in jobs:
        if job['pin_sha256'] != EXPECTED_PIN or job['profile'].get('model'):
            raise RuntimeError(f'pin/model mismatch for {job["id"]}')
        raw_expected, attempt_count = EXPECTED_JOBS[job['id']]
        if Path(job['raw']).is_file() != raw_expected or Path(job['receipt']).exists():
            raise RuntimeError(f'raw/canonical receipt state changed for {job["id"]}')
        archived = delta(job)
        if len(archived['attempts']) != attempt_count or archived['model_proposals']:
            raise RuntimeError(f'private attempt count changed for {job["id"]}')
        job['_archived_delta'] = archived
    if STATE.read_bytes() != pointer_bytes:
        raise RuntimeError('checkpoint moved during staging')

    STAGE.mkdir()
    (STAGE / 'campaign.pointer.before.json').write_bytes(pointer_bytes)
    shutil.copyfile(CONTROL / 'launch.json', STAGE / 'launch.before.json')
    entries = {}
    for job in jobs:
        stem = job['id']
        archive = STAGE / (stem + '.private-delta.json.gz')
        with gzip.open(archive, 'wt', encoding='utf-8') as stream:
            json.dump(job.pop('_archived_delta'), stream, sort_keys=True)
        row = {'delta_sha256': sha(archive), 'delta_bytes': archive.stat().st_size}
        if Path(job['raw']).is_file():
            raw = STAGE / (stem + '.private.json')
            shutil.copyfile(job['raw'], raw)
            row['raw_sha256'] = sha(raw)
            result = json.loads(raw.read_bytes())
            source = Path(result['source'])
            if not source.is_file() or sha(source) != result['source_sha256']:
                raise RuntimeError(f'raw candidate source drift for {stem}')
            candidate = STAGE / (stem + '.candidate.c')
            shutil.copyfile(source, candidate)
            row['candidate_sha256'] = sha(candidate)
        entries[stem] = row
    if STATE.read_bytes() != pointer_bytes:
        raise RuntimeError('checkpoint moved during archive copy')
    receipt = {'kind': 'pre-recovery-archive', 'source_commit': 29289,
               'source_pointer_sha256': EXPECTED_POINTER, 'old_pin_digest': EXPECTED_PIN,
               'old_solver_sha256': EXPECTED_OLD_SOLVER,
               'launch_sha256': sha(STAGE / 'launch.before.json'),
               'jobs': entries}
    (STAGE / 'manifest.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

"""Verify the compact before-image without accessing live campaign state."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from inspect_checkpoint import sha
from stage_recovery import EXPECTED_JOBS, STAGE


def main():
    manifest = json.loads((STAGE / 'manifest.json').read_bytes())
    if (manifest['kind'] != 'pre-recovery-archive'
            or manifest['source_commit'] != 29289
            or set(manifest['jobs']) != set(EXPECTED_JOBS)):
        raise RuntimeError('wrong recovery stage')
    if sha(STAGE / 'campaign.pointer.before.json') != manifest['source_pointer_sha256']:
        raise RuntimeError('archived checkpoint pointer drift')
    if sha(STAGE / 'launch.before.json') != manifest['launch_sha256']:
        raise RuntimeError('archived launch drift')
    counts = {}
    for job_id, row in manifest['jobs'].items():
        archive = STAGE / (job_id + '.private-delta.json.gz')
        if sha(archive) != row['delta_sha256'] or archive.stat().st_size != row['delta_bytes']:
            raise RuntimeError(f'private delta drift: {job_id}')
        with gzip.open(archive, 'rt', encoding='utf-8') as stream:
            delta = json.load(stream)
        raw_expected, expected_count = EXPECTED_JOBS[job_id]
        if (delta['job_id'] != job_id or len(delta['attempts']) != expected_count
                or delta['model_proposals']):
            raise RuntimeError(f'private delta mismatch: {job_id}')
        if raw_expected:
            if (sha(STAGE / (job_id + '.private.json')) != row['raw_sha256']
                    or sha(STAGE / (job_id + '.candidate.c')) != row['candidate_sha256']):
                raise RuntimeError(f'raw/candidate archive drift: {job_id}')
        elif 'raw_sha256' in row or 'candidate_sha256' in row:
            raise RuntimeError(f'unexpected crashed-job raw: {job_id}')
        counts[job_id] = {'attempts': len(delta['attempts']),
                          'edges': len(delta['attempt_edges']),
                          'runs': len(delta['attempt_runs']),
                          'model_proposals': len(delta['model_proposals'])}
    print(json.dumps({'verified': True, 'source_commit': 29289, 'jobs': counts}, indent=2))


if __name__ == '__main__':
    main()

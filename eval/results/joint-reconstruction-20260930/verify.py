"""Audit this throwaway DEV spike's copied evidence without running new compiles."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
ARTIFACTS = HERE / 'portable'
sys.path.insert(0, str(HERE.parents[2]))
from solver import byte_certificate as certificate


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    comparison = json.loads((HERE / 'comparison.json').read_text())
    selected = json.loads((HERE / 'selection.json').read_text())
    census = json.loads((HERE / 'census.json').read_text())
    rows = comparison['rows']
    assert len(rows) == 16
    assert len({(r['function'], r['arm'], r['valid_syntax']) for r in rows}) == 16
    assert not set(selected['functions']) & set(census['heldout'])
    conn = sqlite3.connect((ARTIFACTS / 'attempts.sqlite').as_uri() + '?mode=ro', uri=True)
    attempts = {r[0]: r for r in conn.execute('SELECT id,source_sha256,compiled,exact,source_code FROM attempts')}
    assert len(attempts) == 16
    assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == 0
    groups = {}
    for row in rows:
        index = PurePosixPath(row['receipt']).parent.name
        work = ARTIFACTS / 'compiles' / row['function'] / row['arm'] / index
        receipt = json.loads((work / 'receipt.json').read_text())
        source = (work / 'source.c').read_bytes()
        assert sha(source) == row['source_sha256'] == receipt['source_sha256']
        attempt = attempts[row['attempt_id']]
        assert attempt[1:4] == (row['source_sha256'], int(row['compiled']), int(row['exact']))
        assert sha(attempt[4].encode()) == sha(source)
        target = ARTIFACTS / 'targets' / row['function'] / 'target.o'
        assert sha(target.read_bytes()) == receipt['target_sha256']
        if row['compiled']:
            cert = receipt['verification']
            candidate = work / 'candidate.o'
            assert cert['source_sha256'] == sha(source)
            assert cert['target_sha256'] == sha(target.read_bytes())
            assert cert['candidate_sha256'] == sha(candidate.read_bytes())
            rerun = certificate.certify(target, candidate, source=source.decode())
            assert rerun['exact'] == cert['exact']
            assert receipt['frontend']['source_sha256'] == sha(source)
            assert receipt['frontend']['passed'] is row['frontend_passed']
            groups[(row['function'], row['valid_syntax'], row['arm'])] = candidate
    conn.close()
    same_objects = []
    for (function, valid, arm), left in groups.items():
        if arm != 'baseline':
            continue
        right = groups[(function, valid, 'joint')]
        a = certificate.object_image(left.read_bytes())['sections']
        b = certificate.object_image(right.read_bytes())['sections']
        assert certificate.sections_equivalent(a, b)
        same_objects.append({'function': function, 'valid_syntax': valid})
    assert len(same_objects) == 4
    errors = {arm: sum(len(re.findall('cfe: Error:', r.get('error') or ''))
                       for r in rows if r['arm'] == arm) for arm in ('baseline', 'joint')}
    result = {'attempts_audited': len(attempts), 'source_and_target_hashes_verified': True,
              'certificates_rechecked': len(groups), 'equal_baseline_joint_objects': same_objects,
              'compiler_errors': errors, 'heldout_overlap': [],
              'inference_rows': 0, 'evidence_rows': 0,
              'probe_matches_frozen_run': sha((ARTIFACTS / 'probe-run.py').read_bytes()) ==
                  json.loads((HERE / 'implementation.json').read_text())['probe_sha256']}
    assert result['probe_matches_frozen_run']
    (HERE / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()

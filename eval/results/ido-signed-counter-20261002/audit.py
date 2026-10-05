"""Read-only source/lineage/object audit of the private SBK1 development probe."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/ido-signed-counter-20261002')
sys.path.insert(0, str(PROJECT))
from solver import byte_certificate

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
protocol = json.loads((HERE / 'protocol.json').read_text())
result = json.loads((HERE / 'results.json').read_text())
generated = json.loads((HERE / 'generated-replay.json').read_text())
assert protocol == json.loads((WORK / 'protocol.json').read_text())
assert sha(HERE / 'run.py') == protocol['runner_sha256']
assert all(sha(WORK / 'code' / p) == h for p, h in protocol['code_pins'].items())
assert sha(PROJECT / 'solver/narrow_update.py') == generated['generator_sha256']
for plan in protocol['plans']:
    assert sha(plan['root']) == plan['root_sha256']
    assert all(sha(v['source']) == v['sha256'] for v in plan['variants'])

conn = sqlite3.connect(f'file:{WORK / "trial.sqlite"}?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
rows = list(conn.execute('SELECT a.*, f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr ORDER BY a.id'))
assert len(rows) == 34
by_id = {r['id']: r for r in rows}
exact_receipts = []
for row in rows:
    assert hashlib.sha256(row['source_code'].encode()).hexdigest() == row['source_sha256']
    sampling = json.loads(row['sampling'])
    assert sampling['training_eligible'] is False
    assert sampling['scope'] == 'exposed-development'
    assert row['compiled'] == 1
    parent = row['parent_attempt_id']
    if parent is not None:
        assert parent < row['id'] and by_id[parent]['name'] == row['name']
        assert conn.execute('SELECT 1 FROM attempt_edges WHERE parent_attempt_id=? AND child_attempt_id=?',
                            (parent, row['id'])).fetchone()
    if row['exact'] != 1:
        continue
    cert = sampling['verification']
    assert cert['exact'] and cert['candidate_source_sha256'] == row['source_sha256']
    assert sampling['frontend']['passed'] and cert['frontend']['passed']
    ws = WORK / row['name'] / 'repo/nonmatchings' / row['name']
    certificates = [p for p in ws.glob('*.verification.json')
                    if json.loads(p.read_text()).get('candidate_source_sha256') == row['source_sha256']]
    assert certificates
    for path in certificates:
        saved = json.loads(path.read_text())
        assert saved['candidate_sha256'] == cert['candidate_sha256']
        stem = path.name.removesuffix('.verification.json')
        obj, source = ws / (stem + '.o'), ws / (stem + '.c')
        assert sha(obj) == cert['candidate_sha256']
        assert sha(source) == cert['source_sha256']
        assert sha(ws / 'target.o') == cert['target_sha256']
        assert byte_certificate.certify(ws / 'target.o', obj, source=source.read_text())['exact']
    exact_receipts.append({'id': row['id'], 'function': row['name'],
                           'source_sha256': row['source_sha256'], 'parent': parent})
assert len(exact_receipts) == 8
assert len({r['function'] for r in exact_receipts}) == 2
for record in generated['records']:
    row = by_id[record['attempt_id']]
    assert row['exact'] == 1 and row['source_sha256'] == record['source_sha256']
    assert json.loads(row['sampling'])['generator_label'] == record['label']

cases = []
for case in result['cases']:
    ordinary, targeted, winner = case['ordinary'], case['targeted'], case['winner']
    assert len(ordinary) == 12 and not any(r['accepted'] for r in ordinary)
    assert len(targeted) == 1 and winner['attempt']['accepted'] and winner['repeat']['accepted']
    assert winner['diagnosis']['wrong_ranges'] == 0 and winner['trace_object_correspondence']
    cases.append({'function': case['function'], 'ordinary_children': 12,
        'ordinary_exacts': 0, 'targeted_children_to_exact': 1,
        'wrong_ranges_after': 0, 'trace_object_correspondence': True})
calls = result['diagnostic_calls']
assert len(calls) == 6 and all(c['returncode'] == 0 for c in calls)
assert all(c['ordinary_object_image_equal'] for c in calls if 'ordinary_object_image_equal' in c)
native_seconds = sum(r['seconds'] for c in result['cases']
                     for r in [c['baseline'], *c['ordinary'], *c['targeted'], c['winner']['repeat']])
out = {'game': 'sbk1', 'scope': 'exposed-development with project headers; training-ineligible',
    'scored_attempts': len(rows), 'compiler_failures': 0, 'exact_receipts': exact_receipts,
    'distinct_exact_functions': 2, 'diagnostic_invocations': len(calls), 'cases': cases,
    'source_hashes_verified': True, 'lineage_verified': True, 'frozen_code_pins_verified': True,
    'saved_objects_recertified': True, 'generator_sha256': generated['generator_sha256'],
    'manual_probe_scoring_seconds': native_seconds,
    'diagnostic_seconds': sum(c['seconds'] for c in calls),
    'cost_scope': 'scoring elapsed excludes setup, engineering, generator replay and test time',
    'host_focused_tests_passed': 118, 'native_focused_tests_passed': 176,
    'native_test_seconds': 16.21, 'independent_review': 'alias escape regression fixed; no remaining concrete findings',
    'whole_rom_verified': False, 'clean_transfer_exacts': 0, 'default_enabled': False,
    'campaign_imported': False, 'training_eligible': False,
    'limit': 'unequal finite arms; two previously exposed roots; no held-out transfer claim'}
(HERE / 'audit.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({k: out[k] for k in ('scored_attempts', 'distinct_exact_functions',
    'saved_objects_recertified', 'manual_probe_scoring_seconds', 'diagnostic_invocations')}))
conn.close()

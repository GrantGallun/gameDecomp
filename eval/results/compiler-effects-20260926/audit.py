"""Read-only post-experiment identity, lineage and baseline audit."""
import importlib.util
import json
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('effects_benchmark_audit', HERE / 'benchmark.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
run = Path('/home/grant/decomp/experiments/compiler-effects-20260926')
manifest = b.load_manifest(run)
b.check_code_pins(run, manifest)
model = b.read_sealed(run / 'model.json')
ranking = b.read_sealed(run / 'evaluation-rankings.json')
report = b.read_sealed(run / 'report.json')
dev = {r['tu'] for r in manifest['roots'] if r['split'] == 'development'}
evaluation = {r['tu'] for r in manifest['roots'] if r['split'] == 'evaluation'}
assert not dev & evaluation
assert set(model['model']['development_groups']) == dev
assert all(row['group'] in dev for row in model['model']['examples'])
assert ranking['model_sha256'] == b.digest((run / 'model.json').read_bytes())
attempts = edges = failures = 0
drift = []
exact_rows = []
baseline_exacts = []
for root in manifest['roots']:
    base, path = b.root_paths(run, root)
    result = json.loads(path.read_text())
    baseline = result['baseline']
    children = result['children'] if root['split'] == 'development' else json.loads((base / 'children.json').read_text())['children']
    if abs(baseline['score'] - root['native_score']) > .002:
        drift.append({'function': root['function'], 'old': root['native_score'], 'fresh': baseline['score']})
    if baseline['exact']:
        baseline_exacts.append(root['function'])
    assert len(children) == len(root['proposals'])
    with sqlite3.connect((base / 'attempts.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        for record in [baseline, *children]:
            expected_parent = root['native_attempt_id'] if record is baseline else baseline['receipt_id']
            row = conn.execute('SELECT source_code,source_sha256,parent_attempt_id,compiled,exact,sampling FROM attempts WHERE id=?',
                               (record['receipt_id'],)).fetchone()
            assert row is not None
            assert b.digest(row[0]) == row[1] == record['source_sha256']
            assert row[2] == expected_parent
            assert bool(row[3]) == record['compiled'] and bool(row[4]) == record['exact']
            # attempt_edges records the same actual parent, not a global best.
            edge = conn.execute('SELECT COUNT(*) FROM attempt_edges WHERE child_attempt_id=? AND parent_attempt_id=?',
                                (record['receipt_id'], expected_parent)).fetchone()[0]
            assert edge == 1
            attempts += 1
            edges += edge
            failures += not record['compiled']
            if record is not baseline and b._success(record, baseline, 'exact'):
                exact_rows.append({'function': root['function'], 'split': root['split'],
                                   'ordinal': record['ordinal'], 'source_sha256': record['source_sha256'],
                                   'receipt_id': record['receipt_id'], 'private_database': str(base / 'attempts.sqlite')})
audit = {'passed': not drift and not baseline_exacts, 'source_bound_attempts': attempts,
         'verified_parent_edges': edges, 'compile_failures_logged': failures,
         'baseline_score_drift': drift, 'already_exact_baselines': baseline_exacts,
         'development_tus': len(dev), 'evaluation_tus': len(evaluation),
         'exact_children': exact_rows, 'report_sha256': b.digest((run / 'report.json').read_bytes())}
(HERE / 'audit.json').write_text(json.dumps(audit, indent=2))
print(json.dumps(audit, indent=2))

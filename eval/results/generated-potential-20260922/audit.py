"""Recompute generated paths and verify retained-input and test identities."""
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
freeze = json.loads((OUT/'freeze.json').read_text())
CODE = Path(freeze['code_root'])
sys.path.insert(0, str(CODE))
from solver.capability_map import assess
from solver.capability_operations import from_assessment
from solver.capability_potential import validate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for path, expected in {**freeze['files'], **freeze['test_fixtures']}.items():
        assert sha(CODE/path) == sha(ROOT/path) == expected, path
    results = OUT/'analysis'
    report = json.loads((results/'report.json').read_text())
    prior = OUT.parent/'capability-envelope-20260922/analysis'
    prior_audit = json.loads((prior/'audit.json').read_text())
    assert sha(prior/'audit.json') == report['prior_audit_sha256']
    assert sha(prior/'report.json') == report['prior_report_sha256']
    assert sha(prior/'report.json') == prior_audit['artifact_sha256']['analysis/report.json']
    originals = json.loads((prior/'report.json').read_text())
    for path, expected in report['prior_input_file_sha256'].items():
        assert sha(prior/path) == expected == prior_audit['artifact_sha256']['analysis/'+path]
    for path, expected in report['artifact_sha256'].items():
        assert sha(results/path) == expected, path
    assert len(report['runs']) == 6 and report['new_compiler_calls'] == 0
    for row in report['runs']:
        source = next(r for r in originals['runs'] if r['function'] == row['function'] and r['arm'] == 'theory')
        a = json.loads((prior/source['artifact']).read_text())['assessments'][source['best_id']]
        if row['caller'] != 'theory':
            a = assess(**{**a['inputs'], 'connected': row['caller']})
        expected = from_assessment(a, max_depth=3, max_nodes=256)
        actual = validate(json.loads((results/row['artifact']).read_text()))
        assert actual == expected
        assert row['assessment_sha256'] == a['sha256']
        assert row['receipt_id'] == a['receipt_id']
        assert row['nodes'] == len(actual['nodes']) and row['paths'] == len(actual['paths'])
        assert row['goals'] == actual['goals'] and row['limits'] == actual['limits']
        assert row['multi_step_paths'] == sum(len(p['via']) > 1 for p in actual['paths'])
        assert not actual['training_eligible'] and not actual['global_impossibility_established']
    demo = validate(json.loads((results/'synthetic-chain.json').read_text()))
    assert len(demo['paths']) == 1
    assert demo['paths'][0]['via'] == ['name_fields', 'lift_wide']
    assert demo['paths'][0]['conditions'] == []
    tests = {}
    for platform in ('win32', 'linux'):
        path = OUT/f'tests-{platform}.json'
        receipt = json.loads(path.read_text())
        assert receipt['exit_code'] == 0
        assert receipt['outcomes'] == {'passed': 340, 'failed': 0, 'skipped': 0}
        for module, expected in receipt['modules'].items():
            assert freeze['files'][module] == expected
        tests[platform] = {'outcomes': receipt['outcomes'], 'receipt_sha256': sha(path)}
    audit = {'complete': True, 'retained_states_checked': 3, 'generated_reports_checked': 6,
             'synthetic_examples_checked': 1, 'code_files_checked': len(freeze['files']),
             'test_fixtures_checked': len(freeze['test_fixtures']), 'tests': tests,
             'report_sha256': sha(results/'report.json'), 'audit_script_sha256': sha(Path(__file__)),
             'new_compiler_calls': 0, 'training_eligible': False}
    (results/'audit.json').write_text(json.dumps(audit, indent=2))
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()

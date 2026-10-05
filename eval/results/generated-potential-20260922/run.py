"""Generate potential for retained timer evidence and a labeled symbolic example."""
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
from solver.capability_potential import generate, validate


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def main():
    for path, expected in freeze['files'].items():
        assert sha(CODE/path) == sha(ROOT/path) == expected, path
    prior_dir = OUT.parent/'capability-envelope-20260922/analysis'
    audit = json.loads((prior_dir/'audit.json').read_text())
    assert audit['complete']
    original = json.loads((prior_dir/'report.json').read_text())
    results = OUT/'analysis'
    results.mkdir(exist_ok=False)
    rows = []
    input_hashes = {}
    for row in original['runs']:
        if row['arm'] != 'theory' or row['exact']:
            continue
        path = prior_dir/row['artifact']
        input_hashes[row['artifact']] = sha(path)
        assert input_hashes[row['artifact']] == audit['artifact_sha256']['analysis/'+row['artifact']]
        a = json.loads(path.read_text())['assessments'][row['best_id']]
        for caller in ('theory', 'compile-recovery'):
            current = a if caller == 'theory' else assess(**{**a['inputs'], 'connected': caller})
            report = from_assessment(current, max_depth=3, max_nodes=256)
            validate(report)
            artifact = row['function']+'--'+caller+'.json'
            write(results/artifact, report)
            rows.append({'function': row['function'], 'receipt_id': a['receipt_id'], 'caller': caller,
                         'scope': 'retained caller' if caller == 'theory' else 'counterfactual caller; no execution',
                         'assessment_sha256': current['sha256'], 'artifact': artifact,
                         'nodes': len(report['nodes']), 'paths': len(report['paths']),
                         'goals': report['goals'], 'limits': report['limits'],
                         'multi_step_paths': sum(len(p['via']) > 1 for p in report['paths'])})
    # Symbolic demonstration only: these are not claims that a new named-field
    # conversion exists in the real solver, nor real compiler observations.
    demo = generate({'layout': True, 'named': False, 'wide': False}, [
        {'id': 'name_fields', 'requires': {'layout': True}, 'produces': {'named': True},
         'preserves': ['layout'], 'invalidates': []},
        {'id': 'lift_wide', 'requires': {'layout': True, 'named': True}, 'produces': {'wide': True},
         'preserves': [], 'invalidates': []},
    ], {'wide_candidate': {'wide': True}}, context={'scope': 'synthetic symbolic demonstration'}, max_depth=2)
    validate(demo)
    assert [p['via'] for p in demo['paths']] == [['name_fields', 'lift_wide']]
    write(results/'synthetic-chain.json', demo)
    report = {'complete': True, 'prior_input_file_sha256': input_hashes,
              'prior_report_sha256': sha(prior_dir/'report.json'),
              'prior_audit_sha256': sha(prior_dir/'audit.json'),
              'runs': rows, 'synthetic_example': 'synthetic-chain.json',
              'scope': 'generated conditional paths using retained contracts; no refreshed workspace evidence',
              'new_compiler_calls': 0, 'training_eligible': False,
              'code_files_checked': len(freeze['files'])}
    report['artifact_sha256'] = {p.name: sha(p) for p in sorted(results.glob('*.json'))}
    write(results/'report.json', report)
    for path, expected in freeze['files'].items():
        assert sha(CODE/path) == sha(ROOT/path) == expected, path
    print(json.dumps({'complete': True, 'runs': [{k: r[k] for k in
          ('function', 'caller', 'nodes', 'paths', 'multi_step_paths', 'limits')} for r in rows],
          'new_compiler_calls': 0}, indent=2))


if __name__ == '__main__':
    main()

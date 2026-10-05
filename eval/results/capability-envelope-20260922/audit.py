"""Reconstruct exported assessments and bind them to the prior compiler records."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
FROZEN = json.loads((OUT / 'freeze.json').read_text())
CODE = Path(FROZEN['code_root'])
sys.path.insert(0, str(CODE))

from eval.capability_map import markdown
from eval.repair_graph import build_graph
from eval.search_replay import digest, load_world
from solver.capability_contracts import catalog, validate_catalog
from solver.capability_map import compare, inspect_expectations, validate_assessment
from solver.repair_theory import validate_map


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    for path, expected in FROZEN['files'].items():
        assert sha(CODE / path) == expected, path
        assert sha(ROOT / path) == expected, path
    fixtures = json.loads((OUT / 'test-fixtures.json').read_text())
    for path, expected in fixtures.items():
        assert sha(CODE / path) == sha(ROOT / path) == expected, path

    analysis = OUT / 'analysis'
    report = json.loads((analysis / 'report.json').read_text())
    assert report['sha256'] == digest({k: v for k, v in report.items() if k != 'sha256'})
    assert report['complete'] and not report['training_eligible'] and report['new_compiler_calls'] == 0
    contracts = validate_catalog(json.loads((analysis / 'catalog.json').read_text()))
    assert contracts == catalog(CODE) == catalog(ROOT)
    assert contracts['sha256'] == report['catalog_sha256']
    assert (analysis / 'MAP.md').read_text() == markdown(report)
    prior_path = Path(report['original_report'])
    prior = json.loads(prior_path.read_text())
    assert digest(prior) == report['original_report_sha256']
    for path, expected in prior['artifacts'].items():
        assert sha(prior_path.parent.parent / path) == expected, path
    for path, expected in prior['fixed_files'].items():
        assert sha(Path(prior['code_root']) / path) == expected, path

    db = sqlite3.connect(f"file:{prior['database']}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    attempts = {row['id']: dict(row) for row in db.execute('SELECT * FROM attempts')}
    db.close()
    seen = set()
    counts = Counter()
    transition_counts = Counter()
    owners = {row['id'] for row in contracts['contracts']}
    assert len(report['runs']) == len(prior['runs']) == 30
    for row, old in zip(report['runs'], prior['runs']):
        assert all(row[k] == old[k] for k in ('function', 'arm', 'phase', 'exact'))
        world_path = prior_path.parent / old['world']
        assert sha(world_path) == report['world_file_sha256'][old['world']]
        world = load_world(world_path)
        build_graph([world])
        exported = json.loads((analysis / row['artifact']).read_text())
        assert exported['original_world_sha256'] == digest(world)
        assessments = exported['assessments']
        assert set(assessments) == {node['id'] for node in world['nodes']}
        assert len(assessments) == row['observations']
        transitions = []
        conflicts = []
        for node in world['nodes']:
            assessment = validate_assessment(assessments[node['id']])
            inputs = assessment['inputs']
            assert inputs['source'] == node['source']
            assert inputs['verdict'] == node['verdict']
            assert inputs['context'] == world['context']
            assert inputs['contracts'] == contracts
            assert inputs['connected'] == report['caller']
            assert inputs['function'] == row['function']
            assert not assessment['training_eligible'] and not assessment['global_impossibility_established']
            receipt = assessment['receipt_id']
            assert receipt not in seen
            seen.add(receipt)
            stored = attempts[receipt]
            verdict = node['verdict']
            assert stored['source_code'] == node['source']
            assert stored['source_sha256'] == node['source_sha256']
            assert stored['parent_attempt_id'] == node['parent_receipt_id']
            assert bool(stored['exact']) == verdict['exact']
            assert bool(stored['compiled']) == verdict['compiled']
            assert stored['score'] == verdict['score']
            assert stored['diff_summary'] == verdict.get('raw_diff', verdict.get('diff', ''))
            metadata = json.loads(stored['sampling'])
            assert metadata['training_eligible'] is False
            for key in ('verification', 'frontend', 'compiler_recipe', 'source_attribution'):
                assert metadata.get(key) == verdict.get(key)
            family = node['family']
            if family in {'register_storage', 'address_reuse', 'parameter_reuse'}:
                family = 'storage_search'
            if node['parent'] is not None and family in owners:
                transitions.append(compare(assessments[node['parent']], assessment, contract_id=family))
        for parent, mapping in old.get('theory', {}).get('maps', {}).items():
            validate_map(mapping)
            assessment = assessments[parent]
            assert mapping['inputs']['source'] == assessment['inputs']['source']
            assert mapping['inputs']['verdict'] == assessment['inputs']['verdict']
            for route in mapping['routes']:
                assert route['evidence']['assembly_sha256'] == assessment['assembly_sha256']
            conflicts.extend({'parent': parent, **c,
                              'scope': 'prior proposal observation against current intended catalogue'}
                             for c in inspect_expectations(assessment, mapping['routes']))
        assert transitions == exported['transitions'] == row['transitions']
        assert conflicts == exported['expectation_conflicts'] == row['expectation_conflicts']
        best = assessments[row['best_id']]
        assert best['receipt_id'] == row['best_receipt_id']
        assert row['best_id'] == (old.get('theory', {}).get('best_intake_id') or old['best_id'])
        for key in ('requirements', 'goals', 'composition', 'investigations'):
            assert row[key] == best[key]
        needed = [c for c in best['capabilities'] if c['needed']]
        assert row['capabilities'] == needed
        counts.update(c['status'] for c in needed)
        transition_counts.update(t['status'] for t in transitions)
    assert len(seen) == report['observations_analyzed'] == 100
    assert len(report['runs']) == report['worlds_analyzed']
    assert dict(counts) == report['status_counts']

    tests = {}
    for platform in ('win32', 'linux'):
        path = OUT / f'tests-{platform}.json'
        result = json.loads(path.read_text())
        assert result['exit_code'] == 0
        assert result['outcomes'] == {'passed': 316, 'failed': 0, 'skipped': 0}
        assert len(result['module_sha256']) == 4
        for module, expected in result['module_sha256'].items():
            assert FROZEN['files'][module] == expected
        tests[platform] = {'outcomes': result['outcomes'], 'receipt_sha256': sha(path)}
    artifacts = {p.relative_to(OUT).as_posix(): sha(p)
                 for p in sorted(analysis.rglob('*')) if p.is_file() and p.name != 'audit.json'}
    result = {'complete': True, 'code_files_checked': len(FROZEN['files']),
              'test_fixtures_checked': len(fixtures), 'contracts_checked': len(owners),
              'observations_checked': len(seen), 'worlds_checked': len(report['runs']),
              'new_compiler_calls': 0, 'training_eligible': False,
              'transition_counts': dict(transition_counts), 'tests': tests,
              'artifact_sha256': artifacts, 'audit_script_sha256': sha(Path(__file__))}
    (analysis / 'audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'artifact_sha256'}, indent=2))


if __name__ == '__main__':
    main()

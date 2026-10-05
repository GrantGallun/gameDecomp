"""Verify completed native round receipts and export a compact descriptive report."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest
from eval.research_suite.compiler import accepted
from solver import byte_certificate, regalloc_signature


def compact_result(path, expected, payload, bundle, expected_task_id):
    report = json.loads(path.read_text())
    if report['task'] != expected_task_id:
        raise ValueError('result task differs from the requested task')
    task = next(t for t in payload['tasks'] if t['id'] == report['task'])
    if (report['bundle_sha256'] != manifest.fingerprint(payload)
            or report['environment_sha256'] != manifest.fingerprint(payload['identity'])
            or report['function'] != task['function']
            or report['budget'] != payload['settings']['budget']):
        raise ValueError('result does not bind to the expected bundle')
    if not report['valid'] or report['baseline_gradient'] != expected:
        raise ValueError('invalid or unreproduced result: ' + str(path))
    attempts = [json.loads(l) for l in (path.parent / 'attempts.jsonl').read_text().splitlines()]
    keys = (path.parent / 'keys.jsonl').read_text().splitlines()
    if len(attempts) != report['compiles'] or len(keys) != report['key_calls']:
        raise ValueError('cost denominator mismatch')
    spent = len(attempts) + len(keys) * 0.14
    if not math.isclose(spent, report['budget_spent'], rel_tol=0, abs_tol=1e-9):
        raise ValueError('effective budget accounting mismatch')
    target = manifest.inside(bundle, task['target_object'])
    target_sha = manifest.digest(target.read_bytes())
    if any(r['target_sha256'] != target_sha or r['compile_target'] != task['compile_target'] for r in attempts):
        raise ValueError('attempt does not bind to the expected target')
    sha = manifest.digest(report['best_source'].encode())
    actual = next(r for r in attempts if r['compiled'] and r['source_sha256'] == sha)
    artifact = manifest.inside(path.parent, actual['artifact'])
    if manifest.digest((artifact / 'source.c').read_bytes()) != sha:
        raise ValueError('best source receipt mismatch')
    listing = (artifact / 'candidate_object_dump_normalized.s').read_text()
    if report['exact']:
        certificate = byte_certificate.certify(target, artifact / 'candidate.o', source=report['best_source'])
        if not actual['exact'] or not accepted(certificate, actual['frontend'], report['best_source'], target_sha):
            raise ValueError('exact result lacks its own current byte certificate and frontend pass')
    gradient = list(regalloc_signature.compare((path.parent / 'target.normalized.s').read_text(), listing).gradient)
    if gradient != report['best_compiled_gradient']:
        raise ValueError('returned best does not reproduce recorded compiled gradient')
    events = report['events']
    first_best = next(i + 1 for i, e in enumerate(events) if e.get('compiled') and e.get('gradient') == gradient)
    selected = {d['id'] for d in report['decisions']}
    attempted_parents = {manifest.digest(e['parent_source'].encode())
                         for e in [*events, *report['resolutions']] if e.get('parent_source')}
    return {k: report[k] for k in ('function', 'exact', 'valid', 'budget', 'budget_spent',
        'compiles', 'key_calls', 'compile_seconds', 'key_seconds', 'seconds', 'baseline_gradient',
        'best_compiled_gradient', 'stop', 'production_summary')} | {
        'result': str(path), 'first_best_compile': first_best,
        'maximum_logged_mutation_depth': max((r['depth'] for r in report['production_log'] if 'family' in r), default=0),
        'logged_probes': sum(bool(r.get('probe')) for r in report['production_log']),
        'selected_unique_sources': len(selected),
        'selected_sources_with_child_attempts': len(selected & attempted_parents),
        'selected_parent_scope': 'includes probes made before selection, so this is an upper bound on later expansion',
        'compiles_after_first_best': len(attempts) - first_best,
        'best_source_sha256': sha, 'best_source': str(path.parent / actual['artifact'] / 'source.c'),
        'compile_failures': sum(not r['compiled'] for r in attempts),
        'attempt_errors': sum('error' in r for r in attempts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--extension', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    groups = json.loads((args.experiment / 'selection.json').read_text())
    rows, paired = [], Counter()
    for group in groups:
        i = group['index']
        bundle = args.experiment / f'bundle-{i:02d}'
        payload = manifest.load(bundle)
        if payload['settings']['budget'] != 512:
            raise ValueError('unexpected first-round nominal budget')
        results = []
        for root in group['roots']:
            path = args.experiment / f'run-{i:02d}/t{i:02d}_{root["role"]}/0/evolvability_coalesce/result.json'
            result = compact_result(path, root['gradient'], payload, bundle, f't{i:02d}_{root["role"]}')
            result['role'] = root['role']
            results.append(result)
        original = next(r for r in results if r['role'] == 'original')
        base = tuple(original['best_compiled_gradient'])
        for result in results:
            if result['role'] != 'original':
                grad = tuple(result['best_compiled_gradient'])
                comparison = 'better' if grad < base else 'worse' if grad > base else 'tied'
                paired[result['role'] + '_' + comparison] += 1
        rows.extend(results)
    extensions = []
    if args.extension:
        for role in ('original', 'noop'):
            bundle = args.extension / ('bundle-' + role)
            payload = manifest.load(bundle)
            if payload['settings']['budget'] != 2048:
                raise ValueError('unexpected extension nominal budget')
            task = payload['tasks'][0]
            path = args.extension / f'run-{role}/extended_{role}/0/evolvability_coalesce/result.json'
            extensions.append(compact_result(path, task['provenance']['expected_gradient'], payload, bundle,
                                             'extended_' + role) | {'role': role})
    audit_fields = ('reuse_events', 'audited', 'audit_conclusive', 'audit_unavailable',
                    'expansion_checks', 'expansion_conclusive', 'key_violations')
    output = {'scope': 'adaptive exposed development experiment; individual roots have equal nominal budgets; '
                       'extra roots cost extra; no population yield estimate',
              'functions': len(groups), 'starting_sources': len(rows),
              'first_round_totals': {k: sum(r[k] for r in rows) for k in
                  ('compiles', 'key_calls', 'budget_spent', 'compile_failures', 'attempt_errors')},
              'first_round_audits': {k: sum(r['production_summary'][k] for r in rows) for k in audit_fields},
              'exact_functions': sorted({r['function'] for r in rows if r['exact']}),
              'same_budget_vs_original': dict(paired),
              'roots_improving_on_their_own_start': sum(tuple(r['best_compiled_gradient']) <
                  tuple(r['baseline_gradient']) for r in rows),
              'first_round': rows, 'extensions': extensions}
    manifest.write_json(args.output, output)
    print(json.dumps({k: v for k, v in output.items() if k not in ('first_round', 'extensions')}, indent=2))
    if extensions:
        print(json.dumps([{k: r[k] for k in ('role', 'exact', 'compiles', 'budget_spent',
            'baseline_gradient', 'best_compiled_gradient', 'first_best_compile', 'stop')}
            for r in extensions], indent=2))


if __name__ == '__main__':
    main()

"""Read-only progress/report for the isolated continuation experiment."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import regalloc_signature

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('experiment', type=Path)
parser.add_argument('--output', type=Path)
parser.add_argument('--brief', action='store_true')
args = parser.parse_args()
groups = json.loads((args.experiment / 'selection.json').read_text())
completed, active, outcomes = 0, [], []
attempts, keys, errors, failures = 0, 0, 0, 0
for group in groups:
    i = group['index']
    for root in group['roots']:
        task = f't{i:02d}_{root["role"]}'
        folder = args.experiment / f'run-{i:02d}' / task / '0/evolvability_coalesce'
        if not folder.exists():
            continue
        rows = []
        logfile = folder / 'attempts.jsonl'
        if logfile.exists():
            for line in logfile.read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # last concurrent append may be incomplete
        attempts += len(rows)
        errors += sum('error' in r for r in rows)
        failures += sum(not r['compiled'] for r in rows)
        keyfile = folder / 'keys.jsonl'
        if keyfile.exists():
            keys += len(keyfile.read_text().splitlines())
        result = folder / 'result.json'
        if result.exists():
            r = json.loads(result.read_text())
            completed += 1
            outcomes.append({k: r[k] for k in ('function', 'exact', 'valid', 'compiles', 'key_calls',
                'budget_spent', 'stop', 'best_compiled_gradient', 'baseline_gradient')} |
                {'role': root['role'], 'baseline_reproduced': r['baseline_gradient'] == root['gradient'],
                 'result': str(result)})
        else:
            baseline = folder / 'attempt-00001/candidate_object_dump_normalized.s'
            target = folder / 'target.normalized.s'
            actual = list(regalloc_signature.compare(target.read_text(), baseline.read_text()).gradient) \
                if target.exists() and baseline.exists() else None
            exacts = [r for r in rows if r['exact']]
            active.append({'function': group['function'], 'role': root['role'], 'attempts': len(rows),
                           'baseline_reproduced': actual == root['gradient'] if actual else None,
                           'certified_so_far': len(exacts)})
report = {'functions': len(groups), 'sources': sum(len(g['roots']) for g in groups),
          'completed_sources': completed, 'logged_compiles': attempts, 'logged_keys': keys,
          'compile_failures': failures, 'attempt_errors': errors, 'active': active,
          'completed': outcomes,
          'exact_functions': sorted({r['function'] for r in outcomes if r['valid'] and r['exact']})}
if args.output:
    args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if not args.brief or k != 'completed'}, indent=2))

"""A separately frozen, larger continuation round for the closest first-round pair."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from solver import regalloc_signature


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--role', choices=('original', 'noop'))
    args = parser.parse_args()
    if args.command == 'run':
        if not args.role:
            parser.error('--role required for run')
        runner.run_bundle(args.output / ('bundle-' + args.role), args.repo,
                          args.output / ('run-' + args.role))
        result = json.loads((args.output / ('run-' + args.role) / 'results.json').read_text())[0]
        payload = manifest.load(args.output / ('bundle-' + args.role))
        expected = payload['tasks'][0]['provenance']['expected_gradient']
        if result['baseline_gradient'] != expected:
            raise ValueError('extension baseline does not reproduce')
        print(json.dumps({k: result[k] for k in ('function', 'task', 'valid', 'exact',
              'compiles', 'key_calls', 'budget_spent', 'baseline_gradient',
              'best_compiled_gradient', 'stop')}), flush=True)
        return

    args.output.mkdir(parents=True, exist_ok=False)
    old_bundle = args.experiment / 'bundle-00'
    payload, identity = runner.checked_bundle(old_bundle, args.repo)
    for role in ('original', 'noop'):
        task = next(t for t in payload['tasks'] if t['provenance']['root_role'] == role)
        parent_result = args.experiment / f'run-00/t00_{role}/0/evolvability_coalesce/result.json'
        report = json.loads(parent_result.read_text())
        if not report['valid'] or report['exact']:
            raise ValueError('expected valid non-exact parent run')
        source = report['best_source']
        sha = manifest.digest(source.encode())
        rows = [json.loads(l) for l in (parent_result.parent / 'attempts.jsonl').read_text().splitlines()]
        receipt = next(r for r in rows if r['compiled'] and r['source_sha256'] == sha)
        listing = (parent_result.parent / receipt['artifact'] / 'candidate_object_dump_normalized.s').read_text()
        expected = list(regalloc_signature.compare(
            (parent_result.parent / 'target.normalized.s').read_text(), listing).gradient)
        source_path = args.output / (role + '.c')
        source_path.write_text(source)
        new_task = {**task, 'id': 'extended_' + role, 'source': str(source_path),
                    'target_object': str(manifest.inside(old_bundle, task['target_object'])),
                    'context': str(manifest.inside(old_bundle, task['context'])),
                    'proposals': [], 'provenance': {**task['provenance'],
                        'kind': 'adaptive continuation of a first-round actual-compile best',
                        'expected_gradient': expected, 'parent_source_sha256': sha,
                        'parent_result': str(parent_result),
                        'parent_result_sha256': manifest.digest(parent_result.read_bytes()),
                        'prior_budget': report['budget_spent'],
                        'driver_sha256': manifest.digest(Path(__file__).read_bytes())}}
        config = {'tasks': [new_task], 'settings': {**payload['settings'], 'budget': 2048}}
        manifest.freeze(config, args.output / ('bundle-' + role), identity=identity)
        print(role, expected, 'budget=2048', flush=True)


if __name__ == '__main__':
    main()

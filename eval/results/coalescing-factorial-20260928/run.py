"""Replay exposed development controls through the strict native suite.

Consumes only candidate/target/header artifacts from a prior frozen bundle.
No reference function bodies, winner injection, KB writes or campaign mutation.
The two motivating cases test wiring; they cannot estimate fresh-cohort yield.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler, environment
from solver import scalar_coalesce

FUNCTIONS = {'stepRaceMotionLoopingAnimation', 'stepRaceMotionLoopingJointAnimation'}
ARMS = ['production', 'evolvability', 'production_coalesce', 'evolvability_coalesce']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--input-bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget', type=int, default=128)
    parser.add_argument('--report', type=Path, help='optional compact receipt copy (may be in the Windows workspace)')
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or str(args.output.resolve()).startswith('/mnt/'):
        raise ValueError('native Linux/WSL output outside /mnt required')
    old = manifest.load(args.input_bundle)
    selected = [t for t in old['tasks'] if t['function'] in FUNCTIONS]
    if {t['function'] for t in selected} != FUNCTIONS or len(selected) != 2:
        raise ValueError('expected the two frozen motivating tasks')
    args.output.mkdir(parents=True, exist_ok=False)
    config = {'tasks': [], 'settings': {
        'arms': ARMS, 'seeds': [0], 'budget': args.budget, 'baseline': 'production',
        'search': {'beam': 3, 'depth': 4, 'mutation_preview': 64,
                   'mutation_probes': 2, 'explore_rate': 0.2}}}
    for task in selected:
        row = {**task, 'proposals': []}
        for key in ('source', 'target_object', 'context'):
            row[key] = str(manifest.inside(args.input_bundle, task[key]))
        row['provenance'] = {**task.get('provenance', {}),
            'exposure': 'motivating development case from prior coalescing probe',
            'parent_bundle_sha256': manifest.fingerprint(old)}
        config['tasks'].append(row)
    manifest.write_json(args.output / 'config.json', config)
    identity = environment(args.repo, [t['compile_target'] for t in selected])
    bundle = args.output / 'bundle'
    frozen = manifest.freeze(config, bundle, identity=identity)
    controls = []
    for task in frozen['tasks']:
        runner.checked_bundle(bundle, args.repo)
        source = manifest.task_source(bundle, task)
        compiler = NativeCompiler(args.repo, task, bundle, args.output / 'controls' / task['id'],
                                  budget=13, identity=identity)
        baseline = compiler(source, 'baseline')
        offered = list(scalar_coalesce.variants(source, task['function']))
        for label, family, candidate in offered:
            compiler(candidate, label, source)
        runner.checked_bundle(bundle, args.repo)
        row = {'function': task['function'], 'assistance': task['assistance'],
               'exposure': 'motivating development control', 'offered': len(offered),
               'baseline_compiled': baseline.compiled, 'baseline_exact': baseline.exact,
               'exact_candidates': sum(r['exact'] for r in compiler.rows[1:]),
               'attempts': compiler.rows, 'costs': compiler.costs(),
               'bundle_sha256': manifest.fingerprint(frozen)}
        controls.append(row)
        manifest.write_json(args.output / 'controls.json', controls)
        print(task['function'], 'control:', len(offered), 'offered,', row['exact_candidates'],
              'certified,', compiler.calls, 'compiles', flush=True)
        assert baseline.compiled and not baseline.exact and row['exact_candidates'] > 0, row['function']
    runner.run_bundle(bundle, args.repo, args.output / 'factorial')
    import json
    runs = json.loads((args.output / 'factorial/results.json').read_text())
    manifest.write_json(args.output / 'factorial/evolvability-baseline-summary.json',
                        runner.summarize(runs, baseline='evolvability'))
    manifest.write_json(args.output / 'factorial/coalescing-baseline-summary.json',
                        runner.summarize(runs, baseline='production_coalesce'))
    for r in runs:
        print(r['function'], r['arm'], 'exact=' + str(r['exact']),
              'compiles=' + str(r['compiles']), 'budget_spent=' + str(r['budget_spent']), flush=True)
    if args.report:
        keys = ('function', 'arm', 'seed', 'budget', 'assistance', 'valid', 'exact',
                'compiles', 'key_calls', 'budget_spent', 'budget_overshoot',
                'baseline_gradient', 'best_compiled_gradient', 'policy',
                'production_summary', 'bundle_sha256', 'environment_sha256', 'artifact')
        report = {'kind': 'coalescing-exposed-development-replay', 'output': str(args.output),
                  'scope': 'two previously examined header-assisted cases, not fresh yield',
                  'controls': controls, 'runs': [{k: r[k] for k in keys} for r in runs],
                  'comparisons': {b: runner.summarize(runs, baseline=b) for b in
                                  ('production', 'evolvability', 'production_coalesce')}}
        # Do not silently replace an earlier receipt.
        with args.report.open('x', encoding='utf-8') as out:
            json.dump(report, out, indent=2, allow_nan=False)
            out.write('\n')


if __name__ == '__main__':
    main()

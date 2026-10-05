"""Continue retained coalescing children using the existing native search suite.

No reference C, no new search engine, no campaign/KB writes. Prepare freezes
inputs; workers run disjoint task bundles with strict per-attempt receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from itertools import islice
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import environment
from solver import byte_certificate, regalloc_mutations, regalloc_signature

SAVED = Path('/home/grant/decomp/experiments/coalescing-sweep-20260928/repos')


def choose_roots(root, rows, *, noop=False):
    rows = [r for r in rows if r.get('compiled')]
    if root.get('exact') or any(r.get('exact') for r in rows):
        return []
    improved = [r for r in rows if tuple(r['gradient']) < tuple(root['gradient'])]
    scored = [r for r in rows if r['score'] > root['score']]
    if not improved and not scored:
        return []
    chosen = [dict(root, role='original')]
    if improved:
        chosen.append(dict(min(improved, key=lambda r: (tuple(r['gradient']), -r['score'], r['source'])),
                           role='gradient'))
    if scored:
        best = max(scored, key=lambda r: (r['score'], tuple(-v for v in r['gradient'])))
        if best['source'] not in {r['source'] for r in chosen}:
            chosen.append(dict(best, role='score'))
    if noop:
        candidates = [r for r in rows if r.get('same_object') and r.get('new_moves', 0) > 0
                      and r['source'] not in {x['source'] for x in chosen}]
        if candidates:
            chosen.append(dict(max(candidates, key=lambda r: r['new_moves']), role='noop'))
    return chosen


def neighborhood(source, function, limit=64):
    offers = list(islice(regalloc_mutations.variants(source, function, coalesce=True), limit + 1))
    return {manifest.digest(v[2].encode()) for v in offers[:limit]}, len(offers) > limit


def prepare(args):
    args.output.mkdir(parents=True, exist_ok=False)
    sweep = PROJECT / 'eval/results/coalescing-sweep-20260928/sweep.jsonl'
    rows = [json.loads(line) for line in sweep.read_text().splitlines()]
    groups = []
    for row in rows:
        if row['exact']:
            continue
        name = row['function']
        ws = SAVED / name / 'nonmatchings' / name
        spec = importlib.util.spec_from_file_location('retained_dist', ws / 'dist.py')
        scorer = importlib.util.module_from_spec(spec)
        sys.modules['retained_dist'] = scorer
        spec.loader.exec_module(scorer)
        target = (ws / 'target_object_dump_normalized.s').read_text()
        target_lines = scorer.read_lines(str(ws / 'target_object_dump_normalized.s'))

        def observation(stem):
            src = ws / (stem + '.c')
            obj = ws / (stem + '.o')
            listing = ws / (stem + '_object_dump_normalized.s')
            source = src.read_text()
            compiled = obj.is_file() and listing.is_file()
            cert = byte_certificate.certify(ws / 'target.o', obj, source=source) if compiled else {}
            front = ws / (stem + '.frontend.json')
            frontend = json.loads(front.read_text()) if front.is_file() else {}
            return {'source': source, 'path': str(src), 'sha256': manifest.digest(source.encode()),
                    'compiled': compiled and frontend.get('passed') is True,
                    'exact': cert.get('exact') is True and frontend.get('passed') is True,
                    'gradient': list(regalloc_signature.compare(target, listing.read_text()).gradient) if compiled else [10**9]*3,
                    'score': scorer.score_files(target_lines, scorer.read_lines(str(listing)))[2] if compiled else 0,
                    'same_object': compiled and byte_certificate.certify(ws / (name + '_sbase.o'), obj, source=source)['exact']}

        root = observation(name + '_sbase')
        candidates = [observation(f'{name}_sv{i}') for i in range(row['variants'])]
        selected = choose_roots(root, candidates)
        if not selected:
            continue
        parent_moves, parent_capped = neighborhood(root['source'], name)
        for candidate in candidates:
            if candidate['same_object'] and candidate['compiled']:
                moves, capped = neighborhood(candidate['source'], name)
                candidate.update(new_moves=len(moves - parent_moves), preview_capped=capped,
                                 parent_preview_capped=parent_capped)
        selected = choose_roots(root, candidates, noop=True)
        target_info = json.loads((ws / '.compiler-target.json').read_text())
        best_gradient = min(tuple(x['gradient']) for x in selected)
        groups.append({'function': name, 'ws': str(ws), 'compile_target': target_info['target'],
                       'best_gradient': best_gradient, 'roots': selected})
    groups.sort(key=lambda g: (g['best_gradient'], g['function']))
    # Keep a bounded no-op sample: one per function in the four closest eligible
    # groups. It is an extra exploratory root, not free work in a paired result.
    noop_slots = 4
    for group in groups:
        keep = []
        for row in group['roots']:
            if row['role'] == 'noop':
                if not noop_slots:
                    continue
                noop_slots -= 1
            keep.append(row)
        group['roots'] = keep
    all_targets = [g['compile_target'] for g in groups]
    identity = environment(args.repo, all_targets)
    compact = []
    for i, group in enumerate(groups):
        config = {'tasks': [], 'settings': {'arms': ['evolvability_coalesce'], 'seeds': [0],
                  'budget': args.budget, 'search': {'beam': 4, 'depth': 12, 'mutation_preview': 64,
                                                  'mutation_probes': 2, 'explore_rate': 0.2}}}
        for j, root in enumerate(group['roots']):
            config['tasks'].append({'id': f't{i:02d}_{root["role"]}', 'function': group['function'],
                'compile_target': group['compile_target'], 'source': root['path'],
                'target_object': group['ws'] + '/target.o', 'context': group['ws'],
                'cluster': group['compile_target'], 'assistance': 'unknown',
                'provenance': {'kind': 'exposed retained campaign candidate with project-header context',
                    'source_lineage': 'not independently audited; not clean or unassisted evidence',
                    'root_role': root['role'], 'parent_source_sha256': group['roots'][0]['sha256'],
                    'expected_gradient': root['gradient'], 'prior_score': root['score'],
                    'same_object_as_original': root['same_object'], 'new_moves': root.get('new_moves'),
                    'new_moves_scope': 'distinct source strings in bounded source-only prefix; not proved behavioral novelty',
                    'preview_capped': root.get('preview_capped'),
                    'sweep_sha256': manifest.digest(sweep.read_bytes()),
                    'driver_sha256': manifest.digest(Path(__file__).read_bytes())}})
        # Identity recipes must describe exactly this bundle's target set.
        task_identity = {**identity, 'recipes': {group['compile_target']: identity['recipes'][group['compile_target']]}}
        manifest.freeze(config, args.output / f'bundle-{i:02d}', identity=task_identity)
        compact.append({'index': i, 'function': group['function'], 'best_gradient': group['best_gradient'],
                        'roots': [{k: v for k, v in r.items() if k != 'source'} for r in group['roots']]})
    manifest.write_json(args.output / 'selection.json', compact)
    print(json.dumps({'functions': len(groups), 'starting_sources': sum(len(g['roots']) for g in groups),
                      'budget_per_source': args.budget, 'output': str(args.output)}), flush=True)


def worker(args):
    groups = json.loads((args.output / 'selection.json').read_text())
    for group in groups[args.worker::args.workers]:
        index = group['index']
        dest = args.output / f'run-{index:02d}'
        print('START', index, group['function'], [r['role'] for r in group['roots']], flush=True)
        runner.run_bundle(args.output / f'bundle-{index:02d}', args.repo, dest)
        results = json.loads((dest / 'results.json').read_text())
        for result, root in zip(results, group['roots']):
            if result['baseline_gradient'] != root['gradient']:
                raise ValueError('fresh baseline differs from retained listing: ' + result['task'])
            print('DONE', index, group['function'], root['role'],
                  'exact=' + str(result['exact']), 'compiles=' + str(result['compiles']),
                  'gradient=' + str(result['best_compiled_gradient']), 'stop=' + result['stop'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'worker'])
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget', type=int, default=512)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--worker', type=int, default=0)
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or str(args.output.resolve()).startswith('/mnt/'):
        raise ValueError('native WSL/Linux output outside /mnt required')
    if args.command == 'prepare':
        prepare(args)
    else:
        worker(args)

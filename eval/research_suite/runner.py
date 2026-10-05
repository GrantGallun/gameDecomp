"""Paired, opt-in trials. Allocation variants are small paper-inspired experiments."""
from __future__ import annotations

from collections import defaultdict
import time
from pathlib import Path

from solver import regalloc_mutations, regalloc_signature
from . import manifest, proposals as construction
from .compiler import NativeCompiler, environment
from .search import run_search, PRODUCTION_ARMS


ARMS = ('beam', 'archive', 'explore', 'archive_explore', 'production', 'production_diverse',
        'mutation_count', 'mutation_count_diverse', 'evolvability', 'evolvability_diverse',
        'production_coalesce', 'evolvability_coalesce',
        'pure', 'compose', 'types', 'staged', 'interleaved', 'brackets')


def inherited_assistance(*labels):
    if all(label == 'synthetic' for label in labels):
        return 'synthetic'
    if 'recovered' in labels:
        return 'recovered'
    if 'header_assisted' in labels:
        return 'header_assisted'
    if any(label not in {'declared_unassisted'} for label in labels):
        return 'unknown'
    return 'declared_unassisted'


def run_arm(function, source, compiler, *, arm, budget, seed,
            proposals=(), accesses=(), flows=(), options=None, assistance='unknown'):
    if arm not in ARMS or type(budget) is not int or budget < 1:
        raise ValueError('unsupported arm or nonpositive budget')
    options = dict(options or {})
    if set(options) - {'beam', 'depth', 'archive_size', 'explore_rate', 'mutation_preview', 'mutation_probes', 'scoped_fields'}:
        raise ValueError('unsupported search option')
    if arm in PRODUCTION_ARMS:
        return run_search(function, source, compiler.target_dump, compiler, arm=arm,
                          budget=budget, seed=seed, key=getattr(compiler, 'key', None),
                          same_object=getattr(compiler, 'same_object', None), **options)
    if arm in {'beam', 'archive', 'explore', 'archive_explore'}:
        return run_search(function, source, compiler.target_dump, compiler, arm=arm,
                          budget=budget, seed=seed, **options)
    if arm in {'pure', 'compose', 'types'}:
        report = construction.infer_views(accesses, flows)

        def generate(parent, compiled):
            if arm == 'types':
                for row in construction.type_variants(parent, function, report):
                    yield row['label'], row['family'], row['source']
            elif arm == 'compose':
                for row in construction.compose(parent, lambda s: construction.pure_variants(s, function),
                                                max_depth=2, max_candidates=32):
                    yield '/'.join(p['label'] for p in row['path']), row['family'], row['source']
            else:
                yield from construction.pure_variants(parent, function)
            yield from regalloc_mutations.variants(parent, function, compiled.diff,
                                                   evidence=compiled.evidence,
                                                   scoped_fields=options.get('scoped_fields', True))

        result = run_search(function, source, compiler.target_dump, compiler, arm='beam',
                            budget=budget, seed=seed, generate=generate, **options)
        return {**result, 'arm': arm, 'type_constraints': report if arm == 'types' else None}

    # These arms replay a fixed candidate list. They do not invoke a model or
    # recreate adaptive proposal generation from future execution feedback.
    roots = [(source, {'id': 'baseline', 'assistance': assistance})]
    for index, row in enumerate(proposals):
        candidate = row['source_text']
        if candidate not in {root for root, meta in roots}:
            roots.append((candidate, {'id': row.get('id', f'proposal-{index}'),
                                      'assistance': inherited_assistance(assistance, row.get('assistance', 'unknown'))}))
    roots = roots[:budget]
    result = {'arm': arm, 'exact': False, 'best_source': source, 'compiles': 0,
              'events': [], 'decisions': [], 'phases': [], 'stop': 'exhausted',
              'recorded_generation_seconds': sum(float(p.get('generation_seconds', 0)) for p in proposals),
              'scope': 'fixed proposal allocation; no live model generation or off-policy estimator'}
    best = None
    best_meta = roots[0][1]

    def phase(root, allowance, settings, label, metadata):
        nonlocal best, best_meta
        if allowance <= 0:
            return
        count = len(result['phases'])
        value = run_search(function, root, compiler.target_dump, compiler, arm='beam',
                           budget=allowance, seed=seed + count, **settings)
        # Local state IDs are scoped by phase, avoiding false identity merges.
        result['phases'].append({'label': label, 'budget': allowance, 'lineage': metadata, 'result': value})
        result['compiles'] += value['compiles']
        for event in value['events']:
            result['events'].append({**event, 'phase': count})
            rank = tuple(event['gradient']) if 'gradient' in event else None
            if rank is not None and (best is None or rank < best):
                best, result['best_source'] = rank, event['source']
                best_meta = metadata
        if value['exact']:
            result.update(exact=True, best_source=value['best_source'], stop='exact',
                          winner_assistance=metadata['assistance'], winner_lineage=metadata)

    if arm == 'staged':
        for root, metadata in roots:
            phase(root, 1, options, 'screen', metadata)
            if result['exact']:
                break
        if not result['exact']:
            phase(result['best_source'], budget - result['compiles'], options, 'expand_best', best_meta)
    elif arm == 'interleaved':
        for index, (root, metadata) in enumerate(roots):
            remaining = budget - result['compiles']
            allowance = max(1, remaining // (len(roots) - index))
            phase(root, allowance, options, 'propose_then_expand', metadata)
            if result['exact'] or result['compiles'] >= budget:
                break
    else:
        # Static breadth/depth brackets, deliberately not claimed as full
        # Hyperband/successive halving: an exact hit is too sparse a rung metric.
        first = max(1, budget // 2)
        phase(source, first, {**options, 'beam': 1, 'depth': 8}, 'deep', roots[0][1])
        if not result['exact']:
            phase(source, budget - result['compiles'], {**options, 'beam': 4, 'depth': 2}, 'wide', roots[0][1])
    if not result['exact'] and result['compiles'] >= budget:
        result['stop'] = 'budget'
    return result


def checked_bundle(bundle, repo):
    payload = manifest.load(bundle)
    current = environment(repo, [t['compile_target'] for t in payload['tasks']])
    if current != payload['identity']:
        raise ValueError('compiler, headers, recipe, or implementation drift; freeze a new bundle')
    return payload, current


def compiled_progress(events):
    """Secondary metric from full listings of actual compiles, including the root."""
    baseline = (events[0].get('gradient') if events and events[0].get('compiled') else None)
    gradients = [tuple(row['gradient']) for row in events if row.get('compiled') and row.get('gradient') is not None]
    best = min(gradients) if gradients else None
    return {'baseline_gradient': baseline, 'best_compiled_gradient': list(best) if best is not None else None,
            'gradient_improved': best < tuple(baseline) if best is not None and baseline is not None else None}


def run_bundle(bundle, repo, output, *, arms=None, seeds=None, budget=None):
    payload, identity = checked_bundle(bundle, repo)
    settings = payload['settings']
    arms = list(arms if arms is not None else settings.get('arms', ['production', 'production_diverse']))
    seeds = list(seeds if seeds is not None else settings.get('seeds', [0]))
    budget = budget if budget is not None else settings.get('budget', 32)
    if (not arms or len(set(arms)) != len(arms) or any(a not in ARMS for a in arms)
            or not seeds or any(type(s) is not int or s < 0 for s in seeds)
            or len(set(seeds)) != len(seeds) or type(budget) is not int or budget < 1):
        raise ValueError('invalid arms, seeds, or budget')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runs = []
    manifest.write_json(output / 'run-config.json', {'arms': arms, 'seeds': seeds, 'budget': budget,
                        'bundle_sha256': manifest.fingerprint(payload), 'identity': identity})
    for task in payload['tasks']:
        source = manifest.task_source(bundle, task)
        recorded = [{**p, 'source_text': manifest.inside(bundle, p['source']).read_text(encoding='utf-8')}
                    for p in task['proposals']]
        for seed in seeds:
            for arm in arms:
                # The live repo can change while a suite is running. Refuse a
                # changed input before the arm and invalidate it if drift occurs during it.
                checked_bundle(bundle, repo)
                start = time.monotonic()
                compiler = NativeCompiler(repo, task, bundle, output / task['id'] / str(seed) / arm,
                                          budget=budget, identity=identity)
                setup_seconds = time.monotonic() - start
                result = run_arm(task['function'], source, compiler, arm=arm, budget=budget,
                                 seed=seed, proposals=recorded, accesses=task['accesses'],
                                 flows=task['flows'], options=settings.get('search', {}), assistance=task['assistance'])
                valid, reason = True, None
                try:
                    checked_bundle(bundle, repo)
                except ValueError as exc:
                    valid, reason = False, str(exc)
                if result['compiles'] != compiler.calls:
                    raise AssertionError('search/compiler attempt accounting disagrees')
                if arm in PRODUCTION_ARMS and result['key_calls'] != compiler.key_calls:
                    raise AssertionError('search/compiler key accounting disagrees')
                # Never promote a synthetic callback verdict into a native result.
                source_sha = manifest.digest(result['best_source'].encode())
                certified = any(row['exact'] and row['source_sha256'] == source_sha for row in compiler.rows)
                report = {**result, **compiler.costs(), **compiled_progress(result['events']),
                          'exact': bool(valid and result['exact'] and certified),
                          'valid': valid, 'invalid_reason': reason, 'task': task['id'],
                          'function': task['function'], 'cluster': task['cluster'],
                          'assistance': task['assistance'], 'seed': seed, 'budget': budget,
                          'winner_assistance': result.get('winner_assistance', task['assistance']) if result['exact'] else None,
                          'target_identity': compiler.target_identity, 'setup_seconds': setup_seconds,
                          'seconds': time.monotonic() - start,
                          'bundle_sha256': manifest.fingerprint(payload),
                          'environment_sha256': manifest.fingerprint(identity), 'backend': 'native-ido',
                          'comparison_scope': ('shared-production-engine' if arm in PRODUCTION_ARMS else 'mechanism-only'),
                          'artifact': compiler.output.relative_to(output).as_posix()}
                manifest.write_json(compiler.output / 'result.json', report)
                runs.append(report)
                manifest.write_json(output / 'results.json', runs)
                if not valid:
                    raise ValueError(reason)
    summary = summarize(runs, baseline=settings.get('baseline', 'production'))
    manifest.write_json(output / 'summary.json', summary)
    return summary


def summarize(runs, baseline='production'):
    seen, budgets, rows = set(), set(), []
    for row in runs:
        key = (row['bundle_sha256'], row['task'], row['seed'], row['arm'])
        if key in seen:
            raise ValueError('duplicate task/seed/arm result')
        seen.add(key)
        budgets.add(row['budget'])
        if row.get('valid') is True:
            rows.append(row)
    if len(budgets) > 1:
        raise ValueError('different budgets cannot share one paired summary')
    by_pair = defaultdict(dict)
    for row in rows:
        key = (row['bundle_sha256'], row['environment_sha256'], row['task'], row['seed'])
        by_pair[key][row['arm']] = row
    groups = defaultdict(list)
    for pair in by_pair.values():
        if baseline not in pair:
            continue
        base = pair[baseline]
        for arm, row in pair.items():
            if arm != baseline:
                if row['assistance'] != base['assistance'] or row['cluster'] != base['cluster']:
                    raise ValueError('paired task metadata disagrees')
                groups[(arm, row['assistance'])].append((base, row))
    comparisons = []
    for (arm, assistance), pairs in sorted(groups.items()):
        baseline_functions = {(a['cluster'], a['function']) for a, b in pairs if a['exact']}
        arm_functions = {(b['cluster'], b['function']) for a, b in pairs if b['exact']}
        winner_groups = defaultdict(set)
        for a, b in pairs:
            if b['exact']:
                winner_groups[b.get('winner_assistance') or b['assistance']].add((b['cluster'], b['function']))
        gradient_pairs = [(tuple(a['best_compiled_gradient']), tuple(b['best_compiled_gradient']))
                          for a, b in pairs if a.get('baseline_gradient') is not None
                          and a['baseline_gradient'] == b.get('baseline_gradient')
                          and a.get('best_compiled_gradient') is not None and b.get('best_compiled_gradient') is not None]
        comparisons.append({'arm': arm, 'baseline': baseline, 'assistance': assistance,
                            'shared_production_engine': all(a.get('comparison_scope') == b.get('comparison_scope') ==
                                                            'shared-production-engine' for a, b in pairs),
                            'pairs': len(pairs),
                            'gradient_pairs': len(gradient_pairs),
                            'gradient_unavailable': len(pairs) - len(gradient_pairs),
                            'gradient_better': sum(b < a for a, b in gradient_pairs),
                            'gradient_tied': sum(b == a for a, b in gradient_pairs),
                            'gradient_worse': sum(b > a for a, b in gradient_pairs),
                            'functions': len({a['function'] for a, b in pairs}),
                            'clusters': len({a['cluster'] for a, b in pairs}),
                            'baseline_exact_functions': len(baseline_functions),
                            'arm_exact_functions': len(arm_functions),
                            'additional_exact_functions': len(arm_functions - baseline_functions),
                            'lost_exact_functions': len(baseline_functions - arm_functions),
                            'arm_exact_functions_by_winner_assistance': {key: len(value) for key, value in winner_groups.items()},
                            'wins': sum(not a['exact'] and b['exact'] for a, b in pairs),
                            'losses': sum(a['exact'] and not b['exact'] for a, b in pairs),
                            'both_exact': sum(a['exact'] and b['exact'] for a, b in pairs),
                            'neither_exact': sum(not a['exact'] and not b['exact'] for a, b in pairs),
                            'baseline_compiles': sum(a['compiles'] for a, b in pairs),
                            'arm_compiles': sum(b['compiles'] for a, b in pairs)})
    return {'runs': len(runs), 'valid_runs': len(rows), 'comparisons': comparisons,
            'scope': 'paired descriptive counts; repeated seeds are not independent functions; '
                     'no campaign yield estimate, confidence interval, or off-policy estimate'}

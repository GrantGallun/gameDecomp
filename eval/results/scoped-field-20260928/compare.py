"""Preregister and run a small exposed transfer comparison, using shared engines."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler, environment
from solver import llm, modelrepair, regalloc_signature, scoped_field, workspace

ROOT = Path('/home/grant/decomp/experiments')
OUT = ROOT / 'scoped-field-transfer-20260928'
REPO = Path('/home/grant/decomp/sbk1')
LOCAL = Path(__file__).parent
MODEL = 'gpt-oss:20b'
BUDGET = 128
OPTIONS = {'beam': 4, 'depth': 12, 'mutation_preview': 64, 'mutation_probes': 2, 'explore_rate': .2}


def gradient(target, candidate):
    return tuple(regalloc_signature.compare(target, candidate).gradient)


def prepare():
    excluded = {r['function'] for r in json.loads((ROOT / 'coalescing-continuation-20260928/selection.json').read_text())}
    excluded.update(('stepRaceMotionLoopingAnimation', 'stepRaceMotionLoopingJointAnimation'))
    rows, inventory = [], []
    for path in sorted((ROOT / 'coalescing-sweep-20260928/repos').glob('*/nonmatchings/*/*_sbase.c')):
        name = path.parent.name
        variants = list(scoped_field.variants(path.read_text(), name))
        inventory.append({'function': name, 'excluded_previous_continuation_or_exact': name in excluded,
                          'variants': len(variants)})
        if name in excluded or not variants:
            continue
        target = (path.parent / 'target_object_dump_normalized.s').read_text()
        dump = (path.parent / (name + '_sbase_object_dump_normalized.s')).read_text()
        grad = gradient(target, dump)
        if grad == (0, 0, 0):
            continue
        rows.append({'function': name, 'source': str(path), 'source_sha256': manifest.digest(path.read_bytes()),
                     'expected_gradient': list(grad), 'variants': len(variants),
                     'compile_target': json.loads((path.parent / '.compiler-target.json').read_text())['target']})
    rows.sort(key=lambda r: (r['expected_gradient'], r['function']))
    plan = {'scope': 'Exposed project-header development roots, unknown earlier lineage; not held-out or training eligible',
            'reference_c_used': False, 'selection': 'Exclude previous 17 continuation functions and two known exacts; '
            'require source-only generator fires; choose six smallest retained full-listing gradients, name tiebreak',
            'selection_inventory': inventory, 'eligible_sorted': rows, 'selected': rows[:6],
            'arms': ['existing', 'scoped', 'scoped_model'], 'shared_engine': 'evolvability_coalesce',
            'compile_budget': BUDGET, 'budget_basis': 'compiles + 0.14 * key calls; report action-boundary overshoot',
            'options': OPTIONS, 'seed': 0, 'model': MODEL, 'model_calls_per_function': 2,
            'model_timeout': 180, 'model_num_predict': 4096,
            'primary': 'distinct byte-certified exact functions with frontend pass',
            'secondary': 'best actual full-listing gradient, lexicographic; not semantic correctness',
            'model_policy': 'Two sequential existing-schema proposals from current best measured root; '
            'record invalids/failures, prefer exact then lower full-listing gradient; '
            'shared search from retained best with remaining compiler allowance. No retries or free compiles.',
            'limitation': 'Third arm tests model+packet together; does not isolate packet from model presence. '
            'One seed and small purposively selected development cohort; no population yield claim.'}
    OUT.mkdir(parents=True, exist_ok=False)
    manifest.write_json(OUT / 'preregistration.json', plan)
    manifest.write_json(LOCAL / 'preregistration.json', plan)
    print(json.dumps(plan['selected'], indent=2), flush=True)


def score_module(row):
    path = Path(row['source']).parent / 'dist.py'
    spec = importlib.util.spec_from_file_location('experiment_dist', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, manifest.digest(path.read_bytes())


def run_one(payload, identity, task, arm, original, model_digest):
    bundle = OUT / 'bundle'
    folder = OUT / task['id'] / arm
    compiler = NativeCompiler(REPO, task, bundle, folder, budget=BUDGET, identity=identity)
    source = manifest.task_source(bundle, task)
    observations, generations = [], []
    if arm == 'scoped_model':
        current = compiler(source, 'model-baseline')
        assert current.compiled and not current.exact
        assert gradient(compiler.target_dump, current.dump) == tuple(original['expected_gradient'])
        scorer, scorer_hash = score_module(original)
        def attempt(compiled):
            artifact = folder / compiler.rows[-1]['artifact']
            weighted = scorer.score_files(scorer.read_lines(str(folder / 'target.normalized.s')),
                       scorer.read_lines(str(artifact / 'candidate_object_dump_normalized.s')))[2]
            evidence = compiled.evidence or {}
            return workspace.Attempt(compiled.compiled, weighted, compiled.exact, compiled.diff, '', '',
                verification=evidence.get('verification'), frontend=evidence.get('frontend'),
                compiler_recipe=evidence.get('compiler_recipe'), source_attribution=evidence.get('source_attribution'))
        current_attempt = attempt(current)
        for index in range(2):
            prompt = modelrepair.build_prompt(compiler.target_dump, source, current_attempt,
                function=task['function'], observations=observations)
            prompt += '\nMEASURED FULL-LISTING RESIDUAL (non-register, register instructions, register operands): ' + str(gradient(compiler.target_dump, current.dump))
            prefix = folder / f'proposal-{index}'
            prefix.with_suffix('.prompt.txt').write_text(prompt)
            row = {'index': index, 'model': MODEL, 'model_digest': model_digest, 'seed': index,
                   'parent_source_sha256': manifest.digest(source.encode()), 'prompt_sha256': manifest.digest(prompt.encode()),
                   'scorer_sha256': scorer_hash}
            started = time.monotonic()
            try:
                text, meta = llm.generate(llm.host(), MODEL, prompt, timeout=180, think='low', num_thread=2,
                    num_predict=4096, temperature=.4, seed=index, response_schema=modelrepair.EDIT_SCHEMA,
                    transport_attempts=1)
                prefix.with_suffix('.response.txt').write_text(text)
                manifest.write_json(prefix.with_suffix('.meta.json'), meta)
                row.update(tokens=meta.get('eval_count'), done_reason=meta.get('done_reason'))
                proposal = modelrepair.parse_proposal(text, source=source, truncate_hypothesis=True)
                candidate = modelrepair.apply_proposal(source, proposal)
                row.update(status='valid', hypothesis=proposal.hypothesis)
                compiled = compiler(candidate, 'model:' + proposal.hypothesis, source)
                child_grad = gradient(compiler.target_dump, compiled.dump) if compiled.dump else None
                observations.append({'parent_source_sha256': manifest.digest(source.encode()),
                    'parent_diff_sha256': manifest.digest((current.diff or '').encode()),
                    'child_source_sha256': manifest.digest(candidate.encode()), 'label': proposal.hypothesis,
                    'compiled': compiled.compiled, 'exact': compiled.exact, 'gradient': child_grad})
                row.update(compiled=compiled.compiled, exact=compiled.exact, gradient=child_grad)
                if compiled.exact or (child_grad is not None and child_grad < gradient(compiler.target_dump, current.dump)):
                    source, current = candidate, compiled
                    current_attempt = attempt(current)
            except Exception as exc:
                row.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
            row['seconds'] = time.monotonic() - started
            generations.append(row)
            manifest.write_json(prefix.with_suffix('.receipt.json'), row)
            print(task['function'], arm, 'proposal', row, flush=True)
            if current.exact:
                break
        if current.exact:
            result = {'exact': True, 'best_source': source, 'stop': 'model_exact', 'events': []}
        else:
            result = runner.run_arm(task['function'], source, compiler, arm='evolvability_coalesce',
                budget=BUDGET - compiler.calls, seed=0, options={**OPTIONS, 'scoped_fields': True})
    else:
        result = runner.run_arm(task['function'], source, compiler, arm='evolvability_coalesce', budget=BUDGET,
                                seed=0, options={**OPTIONS, 'scoped_fields': arm == 'scoped'})
    runner.checked_bundle(bundle, REPO)
    assert not any('error' in event for event in result['events']), 'search callback error'
    measured = []
    for row in compiler.rows:
        path = folder / row['artifact'] / 'candidate_object_dump_normalized.s'
        if row['compiled'] and path.is_file():
            measured.append((gradient(compiler.target_dump, path.read_text()), row))
    assert measured[0][0] == tuple(original['expected_gradient']), 'baseline drift'
    best, receipt = min(measured, key=lambda r: (not r[1]['exact'], r[0]))
    (folder / 'best.c').write_text((folder / receipt['artifact'] / 'source.c').read_text())
    result.update(function=task['function'], comparison_arm=arm, costs=compiler.costs(), model_calls=generations,
                  initial_gradient=original['expected_gradient'], best_gradient=list(best), best_receipt=receipt,
                  exact=any(r['exact'] for r in compiler.rows), valid=True, assistance='unknown',
                  bundle_sha256=manifest.fingerprint(payload), charged=compiler.calls + .14 * compiler.key_calls)
    manifest.write_json(folder / 'result.json', result)
    brief = {k: result[k] for k in ('function', 'comparison_arm', 'costs', 'model_calls', 'initial_gradient',
                                    'best_gradient', 'exact', 'valid', 'assistance', 'charged')}
    print(json.dumps(brief), flush=True)
    return brief


def run():
    plan = json.loads((OUT / 'preregistration.json').read_text())
    endpoint = llm.host()
    tags = json.load(urllib.request.urlopen(endpoint + '/api/tags', timeout=5))
    model_digest = next(m['digest'] for m in tags['models'] if m['name'] == MODEL)
    tasks = []
    for i, row in enumerate(plan['selected']):
        path = Path(row['source'])
        assert manifest.digest(path.read_bytes()) == row['source_sha256']
        tasks.append({'id': f't{i:02d}', 'function': row['function'], 'source': row['source'],
            'compile_target': row['compile_target'], 'target_object': str(path.parent / 'target.o'),
            'context': str(path.parent), 'assistance': 'unknown',
            'provenance': {'scope': plan['scope'], 'source_sha256': row['source_sha256'], 'root': row['source']}})
    identity = environment(REPO, [t['compile_target'] for t in tasks])
    payload = manifest.freeze({'tasks': tasks, 'settings': {**plan, 'model_digest': model_digest,
                       'driver_sha256': manifest.digest(Path(__file__).read_bytes())}}, OUT / 'bundle', identity=identity)
    summaries = []
    # Finish compiler-only arms first; local inference is sequential, never parallel.
    for arm in plan['arms']:
        for task, original in zip(payload['tasks'], plan['selected']):
            summaries.append(run_one(payload, identity, task, arm, original, model_digest))
            manifest.write_json(OUT / 'summary.json', summaries)
            manifest.write_json(LOCAL / 'transfer-summary.json', summaries)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run'))
    args = parser.parse_args()
    prepare() if args.action == 'prepare' else run()

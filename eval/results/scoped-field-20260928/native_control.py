"""Verify the new source-only family on its exposed motivating parent."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler, environment
from solver import scoped_field, regalloc_signature

repo = Path('/home/grant/decomp/sbk1')
prior = Path('/home/grant/decomp/experiments/coalescing-continuation-20260928')
output = Path('/home/grant/decomp/experiments/scoped-field-control-20260928')
output.mkdir(parents=True, exist_ok=False)
old_bundle = prior / 'bundle-02'
old = manifest.load(old_bundle)
task = next(t for t in old['tasks'] if t['provenance']['root_role'] == 'gradient')
parent_result = prior / 'run-02/t02_gradient/0/evolvability_coalesce/result.json'
result = json.loads(parent_result.read_text())
assert result['valid'] and result['bundle_sha256'] == manifest.fingerprint(old)
source = result['best_source']
rows = [json.loads(l) for l in (parent_result.parent / 'attempts.jsonl').read_text().splitlines()]
receipt = next(r for r in rows if r['compiled'] and r['source_sha256'] == manifest.digest(source.encode()))
assert (parent_result.parent / receipt['artifact'] / 'source.c').read_text() == source
source_path = output / 'parent.c'
source_path.write_text(source)
identity = environment(repo, [task['compile_target']])
task = {**task, 'id': 'motivation', 'source': str(source_path),
        'target_object': str(manifest.inside(old_bundle, task['target_object'])),
        'context': str(manifest.inside(old_bundle, task['context'])),
        'provenance': {**task['provenance'], 'parent_result': str(parent_result),
                       'parent_source_sha256': manifest.digest(source.encode()),
                       'kind': 'exposed motivating-case regression; not training data'}}
bundle = output / 'bundle'
payload = manifest.freeze({'tasks': [task]}, bundle, identity=identity)
variants = list(scoped_field.variants(source, task['function']))
assert variants, 'generator failed to fire on actual motivating parent'
compiler = NativeCompiler(repo, payload['tasks'][0], bundle, output / 'compiles',
                          budget=1 + len(variants), identity=identity)
compiler(source, 'baseline')
observations = []
for label, family, candidate in variants:
    compiled = compiler(candidate, label, source)
    observations.append({'label': label, 'compiled': compiled.compiled, 'exact': compiled.exact,
        'gradient': list(regalloc_signature.compare(compiler.target_dump, compiled.dump).gradient)
                    if compiled.dump else None})
runner.checked_bundle(bundle, repo)
report = {'kind': 'source-only generator motivating regression', 'assistance': 'unknown',
          'reference_source_used': False, 'source_sha256': manifest.digest(source.encode()),
          'observations': observations, 'costs': compiler.costs(), 'attempts': compiler.rows}
manifest.write_json(output / 'results.json', report)
manifest.write_json(Path(__file__).parent / 'control-receipts.json', report)
print(json.dumps({'observations': observations, 'costs': compiler.costs()}, indent=2))
assert any(r['exact'] for r in observations), 'new family did not close the motivating case'
fixture = Path(__file__).resolve().parents[3] / 'tests/fixtures/scoped_field_aerial.c'
fixture.parent.mkdir(parents=True, exist_ok=True)
fixture.write_text(source)

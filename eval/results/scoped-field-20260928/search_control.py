"""Native shared-search reachability control on the exposed motivating root."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler, environment

repo = Path('/home/grant/decomp/sbk1')
old_bundle = Path('/home/grant/decomp/experiments/scoped-field-control-20260928/bundle')
old = manifest.load(old_bundle)
original = old['tasks'][0]
output = Path('/home/grant/decomp/experiments/scoped-field-search-control-20260928')
output.mkdir(parents=True, exist_ok=False)
task = {**original, **{k: str(manifest.inside(old_bundle, original[k])) for k in ('source', 'context', 'target_object')}}
identity = environment(repo, [task['compile_target']])
bundle = output / 'bundle'
payload = manifest.freeze({'tasks': [task], 'settings': {'budget': 128, 'seed': 0,
    'arm': 'evolvability_coalesce', 'scoped_fields': [False, True], 'kind': 'exposed regression control'}},
    bundle, identity=identity)
reports = []
for enabled in (False, True):
    folder = output / ('enabled' if enabled else 'disabled')
    compiler = NativeCompiler(repo, payload['tasks'][0], bundle, folder, budget=128, identity=identity)
    result = runner.run_arm(task['function'], manifest.task_source(bundle, payload['tasks'][0]),
        compiler, arm='evolvability_coalesce', budget=128, seed=0,
        options={'scoped_fields': enabled, 'depth': 12, 'beam': 4})
    runner.checked_bundle(bundle, repo)
    assert not any('error' in row for row in result['events'])
    manifest.write_json(folder / 'result.json', result)
    row = {'scoped_fields': enabled, 'exact': result['exact'], 'costs': compiler.costs(),
           'charged': result['budget_spent'], 'policy': result['policy'],
           'exact_receipts': [r for r in compiler.rows if r['exact']],
           'family_compile_count': sum(r['label'].startswith('scoped_field:') for r in compiler.rows),
           'bundle_sha256': manifest.fingerprint(payload), 'valid': True}
    reports.append(row)
    manifest.write_json(output / 'summary.json', reports)
    manifest.write_json(Path(__file__).parent / 'search-control.json', reports)
    print(json.dumps({k: v for k, v in row.items() if k not in ('exact_receipts', 'policy')}), flush=True)
assert reports[1]['exact'], 'normal shared search did not reach the known generated exact within allowance'

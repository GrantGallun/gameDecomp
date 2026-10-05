"""Can the existing search finish an original root after the guided timer edit?"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from solver import regalloc_signature

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--experiment', type=Path, required=True)
parser.add_argument('--ablation', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
old_bundle = args.experiment / 'bundle-02'
payload, identity = runner.checked_bundle(old_bundle, args.repo)
task = next(t for t in payload['tasks'] if t['provenance']['root_role'] == 'original')
work = args.ablation / 'original/attempt-00002'
receipt = json.loads((work / 'receipt.json').read_text())
source = (work / 'source.c').read_text()
target = manifest.inside(old_bundle, task['target_object'])
if (not receipt['compiled'] or receipt['source_sha256'] != manifest.digest(source.encode())
        or receipt['target_sha256'] != manifest.digest(target.read_bytes())):
    raise ValueError('ablation source binding mismatch')
expected = list(regalloc_signature.compare((work.parent / 'target.normalized.s').read_text(),
    (work / 'candidate_object_dump_normalized.s').read_text()).gradient)
new_task = {**task, 'id': 'guided_then_search', 'source': str(work / 'source.c'),
            'target_object': str(target), 'context': str(manifest.inside(old_bundle, task['context'])),
            'provenance': {**task['provenance'], 'kind': 'model-guided edit then existing automatic search',
                'expected_gradient': expected, 'model_assisted': True,
                'parent_receipt': str(work / 'receipt.json'),
                'parent_receipt_sha256': manifest.digest((work / 'receipt.json').read_bytes()),
                'driver_sha256': manifest.digest(Path(__file__).read_bytes())}}
manifest.freeze({'tasks': [new_task], 'settings': payload['settings']}, args.output / 'bundle', identity=identity)
runner.run_bundle(args.output / 'bundle', args.repo, args.output / 'run')
report = json.loads((args.output / 'run/results.json').read_text())[0]
if report['baseline_gradient'] != expected:
    raise ValueError('ablation-search baseline changed')
print(json.dumps({k: report[k] for k in ('exact', 'valid', 'compiles', 'key_calls',
    'budget_spent', 'baseline_gradient', 'best_compiled_gradient', 'stop')}), flush=True)

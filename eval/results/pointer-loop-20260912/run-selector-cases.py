"""Replay targeted selectors with the exact saved normal-panel environment."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import argparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import callee_execution, mips_differential as differential, workspace
from selector_cases import evaluate

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--folder',type=Path,required=True)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--indices',type=int,nargs='+',default=[0,21,13])
args=parser.parse_args()
folder = args.folder
summary = json.loads((folder / 'summary.json').read_bytes())
report = json.loads((folder / 'panel-report.json').read_bytes())
ws = Path(summary['workspace'])
repo = ws.parents[1]
name = summary['function']
contracts = report['call_contracts']
environment, admission = callee_execution.load_binary_leaves(repo, report['callee_environment']['leaves'], contracts)
normalize = lambda value: json.dumps(value, sort_keys=True)
assert normalize(environment.manifest()) == normalize(report['callee_environment']), 'saved/fresh callee environments differ'
target = workspace.semantic_assembly((ws / 'target_object_dump_normalized.s').read_text(), ws / 'target.o')
for entry in report['global_extent_admission']['measurements']:
    extent = entry['extent']
    target += '\n# MIPS_DIFF_EXTENT ' + extent['name'] + ' ' + str(extent['size']) + '\n'
cases = tuple(differential.TestCase(**{key: tuple(tuple(x) for x in value)
             if key in {'player_writes','global_writes','entry_registers','call_returns'} else value
             for key, value in case.items()}) for case in report['cases'])
steps = report['exploration_budget_phases'][-1]['per_case_steps']
identity = hashlib.sha256(normalize({'target': target, 'cases': [asdict(case) for case in cases],
    'abi': report['abi'], 'arities': report['call_arities'], 'steps': steps,
    'call_contracts': contracts, 'comparison_policy': report['comparison_policy'],
    'exploration_budget_phases': report['exploration_budget_phases'], 'stress_work': report['stress_work'],
    'callee_environment': environment.manifest(),
    'runner': hashlib.sha256(Path(differential.__file__).read_bytes()).hexdigest()}).encode()).hexdigest()
assert identity == report['panel_sha256'], 'rehydrated panel identity differs from original'
panel = SimpleNamespace(target=target, cases=cases, function=name, arities=report['call_arities'],
                        returns=tuple(report['abi']['return_registers']), max_steps=steps,
                        callee_environment=environment, identity=identity, call_contracts=contracts)
results = []
out = args.out
out.mkdir(exist_ok=False)
for index in args.indices:
    attempt = summary['attempts'][index]
    result = evaluate(panel, ws / ('pointer_loop_%03d.o' % index))
    result.update(index=index, label=attempt['label'], source_sha256=attempt['source_sha256'],
                  attempt_id=attempt['attempt_id'], score=attempt['score'],
                  normal_panel_identity_verified=True)
    (out / ('attempt-%03d.json' % index)).write_text(json.dumps(result, indent=2) + '\n')
    results.append({'index': index, 'label': attempt['label'], 'score': attempt['score'],
                    'counts': result['counts'], 'results': result['results']})
(out / 'summary.json').write_text(json.dumps({'panel_sha256': identity, 'results': results}, indent=2) + '\n')
print(json.dumps(results))

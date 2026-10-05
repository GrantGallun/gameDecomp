"""Export the completed adaptive ablation and recertify its guided-root search."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import accepted
from solver import byte_certificate
from audit_results import compact_result

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ablation', type=Path, required=True)
parser.add_argument('--baseline_only', type=Path, required=True)
parser.add_argument('--search', type=Path, required=True)
parser.add_argument('--export', type=Path, required=True)
parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
args = parser.parse_args()
bundle = args.search / 'bundle'
payload, identity = runner.checked_bundle(bundle, args.repo)
task = payload['tasks'][0]
path = args.search / 'run/guided_then_search/0/evolvability_coalesce/result.json'
search = compact_result(path, task['provenance']['expected_gradient'], payload, bundle, 'guided_then_search')
source = Path(search['best_source']).read_text()
work = Path(search['best_source']).parent
receipt = json.loads((work / 'receipt.json').read_text())
target = manifest.inside(bundle, task['target_object'])
cert = byte_certificate.certify(target, work / 'candidate.o', source=source)
if not search['exact'] or not accepted(cert, receipt['frontend'], source, manifest.digest(target.read_bytes())):
    raise ValueError('guided-root search does not recertify exact')
prefix = task['function'] + '.guided-then-search'
(args.export / (prefix + '.c')).write_text(source)
manifest.write_json(args.export / (prefix + '.certificate.json'), cert)
manifest.write_json(args.export / (prefix + '.frontend.json'), receipt['frontend'])
ablations = []
for origin in (args.baseline_only, args.ablation):
    reports = json.loads((origin / 'results.json').read_text())
    rows = []
    for report in reports:
        attempts = [json.loads(l) for l in (origin / report['role'] / 'attempts.jsonl').read_text().splitlines()]
        if attempts != report['attempts'] or len(attempts) != report['costs']['compiles']:
            raise ValueError('ablation costs do not bind to logs')
        for attempt in attempts:
            work = origin / report['role'] / attempt['artifact']
            candidate = (work / 'source.c').read_text()
            if manifest.digest(candidate.encode()) != attempt['source_sha256']:
                raise ValueError('ablation source receipt mismatch')
            if attempt['exact']:
                check = byte_certificate.certify(target, work / 'candidate.o', source=candidate)
                if not accepted(check, attempt['frontend'], candidate, manifest.digest(target.read_bytes())):
                    raise ValueError('ablation exact does not recertify')
        rows.append({k: report[k] for k in ('role', 'function', 'source_sha256', 'observations', 'costs')})
    ablations.append({'path': str(origin), 'rows': rows})
report = {'scope': 'adaptive model-selected ablation; repeats of one matched function, not additional discoveries',
          'model_assisted': True, 'assistance': 'unknown', 'reference_source_used': False,
          'ablations': ablations, 'guided_then_search': search,
          'total_compiles': search['compiles'] + sum(r['costs']['compiles'] for a in ablations for r in a['rows'])}
manifest.write_json(args.export / 'ablation-receipts.json', report)
print(json.dumps({'compiles': report['total_compiles'], 'guided_then_search_exact': search['exact'],
                  'guided_then_search_compiles': search['compiles']}, indent=2))

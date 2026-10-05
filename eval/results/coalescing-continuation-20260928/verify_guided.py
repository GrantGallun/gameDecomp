"""Bind the separate guided probes to their parents and export verified artifacts."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import accepted
from solver import byte_certificate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--guided', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    args = parser.parse_args()
    args.export.mkdir(parents=True, exist_ok=True)
    reports = json.loads((args.guided / 'results.json').read_text())
    specifications = [(3, 'gradient', False), (2, 'gradient', True), (0, 'noop', True)]
    audited = []
    for report, (index, role, from_best) in zip(reports, specifications, strict=True):
        bundle = args.experiment / f'bundle-{index:02d}'
        payload, identity = runner.checked_bundle(bundle, args.repo)
        task = next(t for t in payload['tasks'] if t['provenance']['root_role'] == role)
        if task['function'] != report['function']:
            raise ValueError('guided function/bundle mismatch')
        parent = {'bundle_sha256': manifest.fingerprint(payload), 'task': task['id'], 'role': role}
        source = manifest.task_source(bundle, task)
        if from_best:
            result_path = args.experiment / f'run-{index:02d}/t{index:02d}_{role}/0/evolvability_coalesce/result.json'
            result = json.loads(result_path.read_text())
            if not result['valid'] or result['bundle_sha256'] != manifest.fingerprint(payload):
                raise ValueError('invalid parent result')
            source = result['best_source']
            receipts = [json.loads(l) for l in (result_path.parent / 'attempts.jsonl').read_text().splitlines()]
            receipt = next(r for r in receipts if r['compiled'] and r['source_sha256'] == manifest.digest(source.encode()))
            if (result_path.parent / receipt['artifact'] / 'source.c').read_text() != source:
                raise ValueError('parent receipt source mismatch')
            parent.update(result=str(result_path), result_sha256=manifest.digest(result_path.read_bytes()),
                          actual_compile=receipt['artifact'])
        sha = manifest.digest(source.encode())
        if sha != report['source_sha256']:
            raise ValueError('guided parent source mismatch')
        parent['source_sha256'] = sha
        folder = args.guided / task['function']
        attempts = [json.loads(l) for l in (folder / 'attempts.jsonl').read_text().splitlines()]
        if attempts != report['attempts'] or len(attempts) != report['costs']['compiles']:
            raise ValueError('guided attempt accounting mismatch')
        if attempts[0]['source_sha256'] != sha or not attempts[0]['compiled']:
            raise ValueError('guided baseline mismatch')
        target = manifest.inside(bundle, task['target_object'])
        target_sha = manifest.digest(target.read_bytes())
        winners = []
        for i, receipt in enumerate(attempts):
            work = manifest.inside(folder, receipt['artifact'])
            candidate = (work / 'source.c').read_text()
            if (receipt['source_sha256'] != manifest.digest(candidate.encode())
                    or receipt['target_sha256'] != target_sha
                    or receipt['compile_target'] != task['compile_target']
                    or (i and receipt['parent_sha256'] != sha)):
                raise ValueError('guided attempt binding mismatch')
            if receipt['exact']:
                cert = byte_certificate.certify(target, work / 'candidate.o', source=candidate)
                if not accepted(cert, receipt['frontend'], candidate, target_sha):
                    raise ValueError('guided exact does not recertify')
                name = task['function']
                (args.export / (name + '.c')).write_text(candidate)
                manifest.write_json(args.export / (name + '.certificate.json'), cert)
                manifest.write_json(args.export / (name + '.frontend.json'), receipt['frontend'])
                winners.append({'label': receipt['label'], 'artifact': str(work),
                                'source_sha256': receipt['source_sha256'], 'certificate': cert,
                                'frontend_passed': receipt['frontend']['passed']})
        audited.append({'function': task['function'], 'model_assisted': True, 'assistance': 'unknown',
                        'reference_source_used': False, 'parent': parent,
                        'costs': report['costs'], 'observations': report['observations'], 'winners': winners})
    manifest.write_json(args.export / 'guided-receipts.json', audited)
    print(json.dumps({'compiles': sum(r['costs']['compiles'] for r in audited),
                      'exact_functions': [r['function'] for r in audited if r['winners']]}, indent=2))


if __name__ == '__main__':
    main()

"""Test whether the final guided timer rewrite also closes the earlier roots.

Exposed, model-selected ablation; not a new discovery or production generator.
The conditional increment proposals themselves use source syntax only.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler
from solver import regalloc_signature, repair_context


def proposals(source, function):
    start, end = repair_context.definition(source, function)
    body = source[start.end():end - 1]
    pattern = re.compile(r'(?P<name>[A-Za-z_]\w*) = (?P<field>[A-Za-z_]\w*->[A-Za-z_]\w*);'
                         r'(?P<between>(?:\s*(?P<other>[A-Za-z_]\w*->[A-Za-z_]\w*) \+= (?:0x[0-9A-Fa-f]+|[0-9]+);)?)\s*'
                         r'if \((?P=name) < (?P<bound>0x[0-9A-Fa-f]+|[0-9]+)\) \{\s*'
                         r'(?P=field) = (?P=name) \+ 1;\s*\}')
    for index, match in enumerate(pattern.finditer(body)):
        if match['other'] == match['field']:
            continue
        replacement = match['between'].lstrip() + ('\n    ' if match['between'] else '')
        replacement += f'if ({match["field"]} < {match["bound"]}) {{\n        {match["field"]} += 1;\n    }}'
        changed = body[:match.start()] + replacement + body[match.end():]
        yield f'conditional-field-increment:{index}', source[:start.end()] + changed + source[end - 1:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = args.experiment / 'bundle-02'
    payload, identity = runner.checked_bundle(bundle, args.repo)
    reports = []
    for role in ('original', 'gradient', 'searched'):
        task = next(t for t in payload['tasks'] if t['provenance']['root_role'] == ('gradient' if role == 'searched' else role))
        source = manifest.task_source(bundle, task)
        parent_result = None
        if role == 'searched':
            parent_result = args.experiment / 'run-02/t02_gradient/0/evolvability_coalesce/result.json'
            result = json.loads(parent_result.read_text())
            if not result['valid'] or result['bundle_sha256'] != manifest.fingerprint(payload):
                raise ValueError('invalid searched parent')
            source = result['best_source']
            receipts = [json.loads(l) for l in (parent_result.parent / 'attempts.jsonl').read_text().splitlines()]
            receipt = next(r for r in receipts if r['compiled'] and r['source_sha256'] == manifest.digest(source.encode()))
            if (parent_result.parent / receipt['artifact'] / 'source.c').read_text() != source:
                raise ValueError('searched parent source mismatch')
        candidates = list(proposals(source, task['function']))
        compiler = NativeCompiler(args.repo, task, bundle, args.output / role,
                                  budget=1 + len(candidates), identity=identity)
        baseline = compiler(source, 'ablation-parent')
        if not baseline.compiled:
            raise ValueError('ablation baseline failed')
        observations = []
        for label, candidate in candidates:
            result = compiler(candidate, label, source)
            row = {'label': label, 'compiled': result.compiled, 'exact': result.exact,
                   'gradient': list(regalloc_signature.compare(compiler.target_dump, result.dump).gradient)
                               if result.compiled and result.dump else None}
            observations.append(row)
            print(role, row, flush=True)
        reports.append({'role': role, 'function': task['function'], 'model_selected_ablation': True,
                        'bundle_sha256': manifest.fingerprint(payload), 'task': task['id'],
                        'parent_result': str(parent_result) if parent_result else None,
                        'parent_result_sha256': manifest.digest(parent_result.read_bytes()) if parent_result else None,
                        'source_sha256': manifest.digest(source.encode()),
                        'observations': observations, 'costs': compiler.costs(), 'attempts': compiler.rows})
        runner.checked_bundle(bundle, args.repo)
        manifest.write_json(args.output / 'results.json', reports)


if __name__ == '__main__':
    main()

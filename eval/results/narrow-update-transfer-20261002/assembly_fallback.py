"""Frozen-function fallback after the global ELF context rejected overlays.

No compiler outcomes or reference bodies select this route. The unchanged repair
is exercised against ordinary and valid-syntax assembly-only m2c output.
"""
from pathlib import Path
import hashlib
import json
import sys
import time

HERE = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    census = json.loads((HERE / 'census.json').read_text())
    work = Path(census['work'])
    protocol = json.loads((work / 'protocol.json').read_text())
    for name, digest in protocol['code_pins'].items():
        assert sha(work / 'code' / name) == digest
    sys.path.insert(0, str(work / 'code'))
    from solver import m2c_input, narrow_update
    roots = next(g for g in census['games'] if g['game'] == 'sbk2')['selected']
    plan = {
        'question': 'Can unchanged narrow_update fire on assembly-only drafts of the same frozen functions?',
        'motivation': 'All ELF-context drafts rejected a cross-overlay address ambiguity before generating any source.',
        'selection': 'Same 24 frozen SBK2 functions; no outcome-based replacements.',
        'drafts': ['existing m2c_input.draft, ordinary syntax', 'existing m2c_input.draft, valid_syntax=True'],
        'candidate_inputs': 'Per-function assembly only; no context headers, ELF context, source answers or model calls.',
        'compiler_evaluations': 0, 'training_eligible': False,
        'generator_sha256': sha(work / 'code/solver/narrow_update.py'),
        'runner_sha256': sha(__file__), 'census_sha256': sha(HERE / 'census.json'),
    }
    plan_path = work / 'assembly-fallback-protocol.json'
    assert not plan_path.exists(), 'Do not overwrite a previous frozen fallback.'
    plan_path.write_text(json.dumps(plan, indent=2) + '\n')
    rows = []
    for root in roots:
        name = root['function']
        folder = work / 'sbk2' / name
        assert sha(folder / 'target.s') == root['assembly_sha256']
        row = {'function': name, 'drafts': [], 'applicable': False}
        for valid in (False, True):
            started = time.monotonic()
            result, meta = m2c_input.draft(Path('/home/grant/decomp/sbk2'), folder / 'target.s', valid_syntax=valid)
            assert meta['context'] == 'assembly only'
            item = {'valid_syntax': valid, 'returncode': result.returncode, 'metadata': meta,
                    'stderr': result.stderr, 'seconds': time.monotonic() - started}
            if result.returncode == 0:
                path = folder / ('assembly-valid.c' if valid else 'assembly-ordinary.c')
                path.write_text(result.stdout)
                proposals = list(narrow_update.variants(result.stdout, name))
                item.update(source=str(path), source_sha256=sha(path), proposals=len(proposals))
                row['applicable'] |= bool(proposals)
            row['drafts'].append(item)
        rows.append(row)
        print(json.dumps({'function': name, 'applicable': row['applicable'],
                          'successful_drafts': sum(d['returncode'] == 0 for d in row['drafts'])}), flush=True)
    report = {'status': 'assembly_only_census_complete', 'rows': rows,
              'applicable_functions': sum(r['applicable'] for r in rows),
              'compiler_evaluations': 0, 'model_calls': 0, 'training_eligible': False,
              'protocol': str(plan_path), 'protocol_sha256': sha(plan_path)}
    (HERE / 'assembly-fallback.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'complete': True, 'applicable_functions': report['applicable_functions']}))


if __name__ == '__main__':
    main()

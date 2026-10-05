"""Use the existing byte-view lowerer on frozen assembly-only valid drafts."""
from pathlib import Path
import hashlib
import json
import sys

HERE = Path(__file__).resolve().parent


def main():
    census = json.loads((HERE / 'census.json').read_text())
    work = Path(census['work'])
    sys.path.insert(0, str(work / 'code'))
    from solver import m2c_byte_view, narrow_update, regalloc_mutations
    fallback = json.loads((HERE / 'assembly-fallback.json').read_text())
    rows = []
    for root in fallback['rows']:
        name = root['function']
        draft = next(d for d in root['drafts'] if d['valid_syntax'])
        folder = Path(draft['source']).parent
        source = Path(draft['source']).read_text()
        assert hashlib.sha256(source.encode()).hexdigest() == draft['source_sha256']
        row = {'function': name, 'status': 'declined'}
        try:
            report = m2c_byte_view.lower(source, name, target_assembly=(folder / 'target.s').read_text())
            candidate = report['source']
            path = folder / 'assembly-lowered.c'
            assert not path.exists()
            path.write_text(candidate)
            row.update(status='lowered', source=str(path), source_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                       direct_proposals=len(list(narrow_update.variants(candidate, name))),
                       lowerer_sha256=hashlib.sha256((work / 'code/solver/m2c_byte_view.py').read_bytes()).hexdigest())
        except ValueError as exc:
            row['reason'] = str(exc)
        rows.append(row)
    result = {'status': 'existing_byte_view_lowering_complete', 'rows': rows,
              'compiler_evaluations': 0, 'model_calls': 0, 'training_eligible': False,
              'repair_generator_modified': False}
    (HERE / 'lowered-fallback.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'lowered': sum(r['status'] == 'lowered' for r in rows),
                      'directly_applicable': sum(r.get('direct_proposals', 0) > 0 for r in rows),
                      'declines': [{'function': r['function'], 'reason': r['reason']} for r in rows if 'reason' in r]}))


if __name__ == '__main__':
    main()

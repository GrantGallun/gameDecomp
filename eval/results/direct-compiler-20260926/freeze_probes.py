"""Freeze source-bound interventions before observing their compiler outputs."""
import hashlib
import json
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
NAME = 'drawControllerPakFileDeleteConfirmOptions'
DB = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite')
SHA = '24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72'

def main():
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as conn:
        source, addr, sha = conn.execute('SELECT source_code,func_addr,source_sha256 FROM attempts WHERE id=157771').fetchone()
    assert sha == SHA == hashlib.sha256(source.encode()).hexdigest()
    pair = '        var_v1 = 0x80;\n        var_t0 = 0x80;'
    decl = '    u16 var_v1;\n    u16 var_t0;'
    swapped = '        var_t0 = 0x80;\n        var_v1 = 0x80;'
    assert source.count(pair) == 2 and source.count(decl) == 1
    proposals = []
    def add(label, child, reason):
        assert child != source and child not in [p['source'] for p in proposals]
        proposals.append({'label': label, 'source': child,
                          'source_sha256': hashlib.sha256(child.encode()).hexdigest(), 'reason': reason})
    changed_decl = source.replace(decl, '    u16 var_t0;\n    u16 var_v1;', 1)
    add('declaration_order', changed_decl, 'Measure whether local declaration identity changes allocation without changing execution order.')
    add('declaration_and_first_pair', changed_decl.replace(pair, swapped, 1),
        'Reverse the emitting statement order while separately changing declaration identity; test whether physical roles can stay fixed.')
    for label, replacement in (
        ('chain_v1_outer', '        var_v1 = var_t0 = 0x80;'),
        ('chain_t0_outer', '        var_t0 = var_v1 = 0x80;'),
        ('comma_v1_first', '        var_v1 = 0x80, var_t0 = 0x80;'),
        ('comma_t0_first', '        var_t0 = 0x80, var_v1 = 0x80;'),
        ('redundant_v1_seed', '        var_v1 = 0;\n' + swapped),
        ('redundant_t0_seed', '        var_t0 = 0;\n' + pair),
    ):
        add(label, source.replace(pair, replacement, 1),
            'Measure source value construction and first-definition order while preserving the same two default values and later uses.')
    inner = pair + '\n        if (gControllerPakMenuState.confirmChoice == 0) {\n            var_v1 = 0x100;\n        } else {\n            var_t0 = 0x100;\n        }'
    assert source.count(inner) == 1
    for label, yes, no in (
        ('defaults_in_arms', 'var_v1 = 0x100;\n            var_t0 = 0x80;', 'var_v1 = 0x80;\n            var_t0 = 0x100;'),
        ('defaults_in_arms_reversed', 'var_t0 = 0x80;\n            var_v1 = 0x100;', 'var_t0 = 0x100;\n            var_v1 = 0x80;'),
    ):
        replacement = '        if (gControllerPakMenuState.confirmChoice == 0) {\n            ' + yes + '\n        } else {\n            ' + no + '\n        }'
        add(label, source.replace(inner, replacement, 1),
            'Expose identical path values using explicit branch definitions; observe whether common defaults move without exchanging later roles.')
    for label, var in (('hoist_v1_default', 'var_v1'), ('hoist_t0_default', 'var_t0')):
        needle = '    if (gControllerPakMenuState.state == 2) {'
        child = source.replace(needle, f'    {var} = 0x80;\n' + needle, 1)
        child = child.replace(f'        {var} = 0x80;\n', '', 1)
        add(label, child, 'Change only one default definition location; the global read is nonvolatile and the local is unobserved until after both tests.')
    manifest = {'kind': 'direct-compiler-probe-v1', 'repo': '/home/grant/decomp/sbk1',
                'kb': '/home/grant/decomp/kb-sbk1.sqlite', 'native_db': str(DB),
                'function': NAME, 'addr': addr, 'native_attempt_id': 157771,
                'source_sha256': SHA, 'expected_baseline_score': 99.889,
                'training_eligible': False, 'proposals': proposals,
                'evidence': ['baseline.pre-as1.s', 'prior_swap.pre-as1.s',
                             '../frontier-run-20260926/range-split/TRACE.json']}
    with (HERE / 'manifest.json').open('x') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
    (HERE / 'manifest.sha256').write_text(hashlib.sha256((HERE / 'manifest.json').read_bytes()).hexdigest() + '\n')
    print(json.dumps({'proposals': len(proposals), 'labels': [p['label'] for p in proposals]}))

if __name__ == '__main__':
    main()

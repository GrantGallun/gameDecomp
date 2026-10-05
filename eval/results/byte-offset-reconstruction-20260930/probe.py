"""Bounded DEV source hypotheses; no KB writes or production generator wiring."""
from pathlib import Path
import argparse
from dataclasses import asdict
import hashlib
import json
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
PRIOR = ROOT / 'eval/results/joint-reconstruction-20260930'
CONFIG = {
    'drawMenuSpriteClipped': ('sp94', 'temp_t4', 'var_t2', 't4'),
    'drawMenuSpriteWithAlphaClipped': ('sp9C', 'temp_t2', 'sp5C', 't2'),
}

def sha(value):
    return hashlib.sha256(value).hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')

def propose(source, fn, assembly, *, byte_units):
    """Two explicitly scoped sprite hypotheses, requiring their target motifs.

    This is a slice-specific experiment, not a deployable general parser. The
    target motifs authorize testing; compiler/semantic gates remain decisive.
    No complete struct layout or allocation extent is claimed.
    """
    report = {'source': source, 'changes': [], 'declines': [],
              'source_sha256': sha(source.encode()), 'assembly_sha256': sha(assembly.encode())}
    if fn not in CONFIG:
        report['declines'].append('unchanged wrapper control')
        return report
    table, cursor, index, register = CONFIG[fn]
    # Keep raw original instructions as immutable witnesses, strip only formatting.
    lines = [re.sub(r'\s+', '', line.split('*/')[-1]).replace('$', '')
             for line in assembly.splitlines() if '*/' in line]
    witnesses = [r'lwt6,0x4\(a2\)', r'sllt7,t6,3', r'addut8,t7,a2',
                 r'addiut9,t8,0x8', 'addu'+register+r',a2,t7',
                 r'addiu'+register+','+register+r',0x8']
    found = [[line for line in lines if re.fullmatch(pattern, line)] for pattern in witnesses]
    if not all(found):
        report['declines'].append('missing target stride/header/subobject witnesses')
        return report
    # Assert the complete observed source family before applying any edit.
    edits = [(f'void *{name};', f'u8 *{name};') for name in (table, cursor, cursor+'_2')]
    store = re.compile(r'(\w+->unk4 = )\('+index+r' << 5\) \+ '+table+r';')
    matches = list(store.finditer(source))
    if len(matches) != 1 or any(source.count(before) != 1 for before, _ in edits):
        report['declines'].append('source cursor/store family is absent or ambiguous')
        return report
    edits.append((matches[0][0], matches[0][1]+f'(T1 *)(({index} << 5) + {table});'))
    if byte_units:
        edits.extend([
            (f'{table} = (arg2->unk4 * 8) + arg2 + 8;',
             f'{table} = (arg2->unk4 * 8) + (u8 *)arg2 + 8;'),
            (f'{cursor} = arg2 + ((u16) arg3 * 8);',
             f'{cursor} = (u8 *)arg2 + ((u16) arg3 * 8);'),
        ])
    if any(source.count(before) != 1 for before, _ in edits):
        report['declines'].append('source indexed-address family is absent or ambiguous')
        return report
    for before, after in edits:
        source = source.replace(before, after, 1)
        report['changes'].append({'before': before, 'after': after})
    report.update(source=source, candidate_sha256=sha(source.encode()), witnesses=found,
                  authority='candidate hypothesis; no layout fact or runtime domain claim')
    return report

def controls():
    rows = []
    for fn in CONFIG:
        source = (PRIOR/'portable/drafts'/fn/'joint/valid/source.c').read_text()
        asm = (PRIOR/'portable/targets'/fn/'target.s').read_text()
        good = propose(source, fn, asm, byte_units=True)
        assert len(good['changes']) == 6 and good['source'] != source
        bad_target = propose(source, fn, asm.replace('$t7, $t6, 3', '$t7, $t6, 4'), byte_units=True)
        assert not bad_target['changes'] and bad_target['source'] == source
        altered = source.replace('arg2 + ((u16) arg3 * 8)', 'arg2 + ((u16) arg3 * 16)')
        bad_source = propose(altered, fn, asm, byte_units=True)
        assert not bad_source['changes'] and bad_source['source'] == altered
        rows.append({'function': fn, 'motivating_emission': True, 'wrong_stride_declined': True,
                     'changed_source_declined': True})
    # Finite arithmetic sanity check, not full-function differential execution.
    for base in (0x80010000, 0x80100000):
        for index in (0, 1, 2, 127, 32767, 65535):
            assert base + index*8 + 8 == base + (index << 3) + 8
    return {'passed': True, 'rows': rows, 'scope': 'emission/decline guards and finite address arithmetic only'}

def run(repo, output):
    from eval.research_suite.compiler import NativeCompiler, environment
    from solver import workspace, signals
    output.mkdir(parents=True, exist_ok=False)
    selected = json.loads((PRIOR/'selection.json').read_text())
    census = json.loads((PRIOR/'census.json').read_text())
    fns = selected['functions']
    target = 'build/src/menu/renderer/menu_renderer.o'
    write(output/'preregistration.json', {
        'kind': 'byte-offset-dev-spike', 'functions': fns,
        'arms': ['baseline', 'cursor-syntax', 'byte-units'], 'budget_per_function_per_arm': 1,
        'selection': 'same exposed DEV slice as prior joint experiment; no heldout claims',
        'primary': 'frontend-valid compilation and byte-exact object certificate',
        'treatment': 'three u8 cursor views and explicit store cast; byte-units additionally casts arg2 at both byte additions',
        'probe_sha256': sha(Path(__file__).read_bytes()), 'training_eligible': False,
        'runtime_validation': 'no full-function differential execution; no all-input semantic claim'})
    shutil.copytree(PRIOR/'portable/targets', output/'targets')
    (output/'empty-context').mkdir()
    identity = environment(repo, [target])
    write(output/'environment.json', identity)
    write(output/'controls.json', controls())
    conn = sqlite3.connect(output/'attempts.sqlite')
    conn.executescript((ROOT/'kb/schema.sql').read_text())
    for fn in fns:
        row = census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)', (row['tu_id'], target))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)',
                     (row['addr'], fn, row['tu_id'], row['insn_count']))
    conn.commit()
    rows = []
    for fn in fns:
        parent = (PRIOR/'portable/drafts'/fn/'joint/valid/source.c').read_text()
        assembly = (output/'targets'/fn/'target.s').read_text()
        parent_id = None
        for arm in ('baseline', 'cursor-syntax', 'byte-units'):
            generation = {'source': parent, 'changes': [], 'declines': []} if arm == 'baseline' else propose(parent, fn, assembly, byte_units=arm == 'byte-units')
            source = generation.pop('source')
            task = {'function': fn, 'compile_target': target,
                    'target_object': f'targets/{fn}/target.o', 'context': 'empty-context'}
            compiler = NativeCompiler(repo, task, output, output/'compiles'/fn/arm, budget=1, identity=identity)
            result = compiler(source, arm, parent_source=parent if arm != 'baseline' else None)
            measured = compiler.rows[-1]
            row = {'function': fn, 'arm': arm, 'compiled': result.compiled, 'exact': result.exact,
                   'frontend_passed': (measured.get('frontend') or {}).get('passed') is True,
                   'generation': generation, 'receipt': str(compiler.output/measured['artifact']/'receipt.json'),
                   'source_sha256': measured['source_sha256'], 'error': measured.get('error')}
            if result.compiled:
                row['faults'] = asdict(signals.analyse(result.diff, 0))
            att = workspace.Attempt(result.compiled, 0, result.exact, result.diff or '',
                measured.get('error') or '', '', verification=measured.get('verification'),
                frontend=measured.get('frontend'), compiler_recipe=compiler.recipe)
            row['attempt_id'] = workspace.record_attempt(conn, fn, source, att,
                strategy='byte-offset-dev-spike:'+arm, run_id=output.name, run_kind='dev-spike',
                wall_ms=int(measured['seconds']*1000), parent_attempt_id=parent_id if arm != 'baseline' else None,
                extra={'generation': generation, 'training_eligible': False, 'score_available': False})
            if arm == 'baseline':
                parent_id = row['attempt_id']
            rows.append(row)
            write(output/'comparison.partial.json', {'rows': rows})
            print(json.dumps({k: row[k] for k in ('function', 'arm', 'compiled', 'frontend_passed', 'exact')}), flush=True)
    conn.close()
    summary = {arm: {key: sorted({r['function'] for r in rows if r['arm'] == arm and r[key]})
                     for key in ('compiled', 'frontend_passed', 'exact')}
               for arm in ('baseline', 'cursor-syntax', 'byte-units')}
    write(output/'comparison.json', {'summary': summary, 'rows': rows, 'deployed': False,
          'production_kb_modified': False, 'model_calls': 0})
    print(json.dumps(summary), flush=True)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    ap.add_argument('--output', type=Path)
    ap.add_argument('--controls', action='store_true')
    args = ap.parse_args()
    if args.controls:
        print(json.dumps(controls()))
    else:
        run(args.repo, args.output)

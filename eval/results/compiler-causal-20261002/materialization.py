"""Diagnostic follow-up: explicit memory ownership, not variable-name inlining."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / 'inputs.json').read_text())
WORK = Path(INPUTS['work'])
sys.path.insert(0, INPUTS['code'])
from solver import regalloc_signature, uopt_diagnosis, register_protocol, workspace

TRACE_CC = Path('/home/grant/decomp/tools-src/ido-trace/cc')


def main():
    name = sys.argv[1]
    variant_group = sys.argv[2] if len(sys.argv) > 2 else 'memory-view'
    item = next(x for x in INPUTS['inputs'] if x['function'] == name)
    folder = WORK / name
    repo = folder / 'repo'
    ws = workspace.bootstrap(repo, name)
    source = Path(item['source']).read_text()
    baseline = json.loads((folder / 'result.json').read_text())
    target = (ws / 'target_object_dump_normalized.s').read_text()
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    # Predicate fixed before compiling: byte/halfword views already present in the candidate.
    memory = list(re.finditer(r'\(\*\((?:u8|s8|u16|s16)\s*\*\)', source))
    proposals = []
    for i, match in enumerate(memory):
        patch = source[:match.start()] + source[match.start():match.end()].replace('(*(', '(*(volatile ', 1) + source[match.end():]
        proposals.append((f'volatile-view:{i}', patch))
    all_views = re.sub(r'\(\*\((u8|s8|u16|s16)\s*\*\)', r'(*(volatile \1 *)', source)
    proposals.insert(0, ('volatile-views:all', all_views))
    if variant_group == 'increment':
        assert name == 'randomNextObject'
        from solver import regalloc_mutations
        begin, end = regalloc_mutations._body(source, name)
        access = '(*(u8 *)((u8 *)(arg0) + 0x518))'
        forms = [f'return *(&gRandomTable + ++{access});',
                 f'return *(&gRandomTable + (u8)({access}++ + 1));',
                 f'{access}++;\n    return *(&gRandomTable + {access});',
                 f'return *(&gRandomTable + ({access} = (u8)({access} + 1)));']
        proposals = [('increment:expression:' + str(i), source[:begin] + '\n    ' + body + '\n' + source[end:])
                     for i, body in enumerate(forms)]
        for ctype in ('u8', 'u16', 'u32', 's32'):
            for expression in ('++value', 'value++', 'value += 1'):
                bump = f'value = {access};\n    {expression};\n    {access} = value;\n    return *(&gRandomTable + (value & 0xFF));'
                proposals.append((f'increment:local:{ctype}:{expression}', source[:begin] + f'\n    {ctype} value;\n    ' + bump + '\n' + source[end:]))
    elif variant_group == 'counter-reread':
        assert name == 'updateEndingCreditsIdleSparkle'
        from solver import regalloc_mutations
        begin, end = regalloc_mutations._body(source, name)
        body = source[begin:end]
        tail = body[body.index('    if (gEndingCreditsSequencePhase != 7)'):]
        a = '(*(u16 *)((unsigned char *)arg0 + 0x1E))'
        b = '(*(u16 *)((unsigned char *)arg0 + 0x1C))'
        assert a in body and b in body and '(temp_t0 & 0xFFFF) == 5' in body
        def update(view, form):
            return view + '++;' if form == 'post' else view + ' += 1;' if form == 'compound' else view + ' = ' + view + ' + 1;'
        proposals = []
        for outer in ('post', 'compound'):
            for inner in ('post', 'compound', 'assignment'):
                for masked in (False, True):
                    compare = f'({b} & 0xFFFF)' if masked else b
                    text = f'\n    {update(a, outer)}\n    if ({a} == 4) {{\n        {a} = 0;\n        {update(b, inner)}\n        if ({compare} == 5) {{\n            {b} = 0;\n        }}\n    }}\n' + tail
                    proposals.append((f'counter-reread:{outer}:{inner}:{masked}', source[:begin] + text + source[end:]))
    seen, rows = {source}, []
    start = time.monotonic()
    for label, code in proposals[:16]:
        if code in seen:
            continue
        seen.add(code)
        tag = f'{variant_group.replace("-", "_")}_{len(rows)}'
        attempt = workspace.score(ws, repo, tag, code, conn=conn, func=name,
            strategy='compiler-causal:memory-ownership:' + label, run_id='compiler-causal-20261002',
            parent_attempt_id=baseline['baseline_attempt_id'], relation='memory-ownership-intervention',
            extra={'training_eligible': False, 'assistance': item['assistance'],
                   'hypothesis': 'Explicit value materialization intervention; no claim to original source types or qualifiers'})
        conn.commit()
        dump = (ws / (tag + '_object_dump_normalized.s')).read_text() if attempt.compiled else ''
        certified = bool(attempt.compiled and (attempt.frontend or {}).get('passed') and
                         (attempt.exact or (attempt.verification or {}).get('function_boundary', {}).get('function_exact')))
        row = {'label': label, 'attempt_id': attempt.receipt_id, 'compiled': attempt.compiled,
               'frontend_passed': (attempt.frontend or {}).get('passed'), 'object_exact': attempt.exact,
               'function_exact': (attempt.verification or {}).get('function_boundary', {}).get('function_exact'),
               'certified': certified, 'source_sha256': hashlib.sha256(code.encode()).hexdigest(),
               'gradient': list(regalloc_signature.compare(target, dump).gradient) if dump else None}
        if dump:
            # Every observation, including a regression, gets a before/after compiler diagnosis.
            texts = uopt_diagnosis.traced_compile(ws, repo, code, TRACE_CC, name)
            trace = folder / (tag + '-trace')
            trace.mkdir(exist_ok=False)
            (trace / 'source.c').write_text(code)
            (trace / 'candidate.s').write_text(dump)
            if texts:
                for key, value in texts.items():
                    (trace / (key + '.txt')).write_text(value)
                diagnosis = uopt_diagnosis.diagnose(target, dump, texts['level5'], texts['level6'], texts['ugen'], name)
                row['diagnostic_compiles'] = 3
                row['diagnosis'] = diagnosis
                row['protocol'] = register_protocol.analyse(target, dump, texts['level5'], texts['level6'], texts['ugen'], name)
            else:
                row['diagnostic_compiles'] = None
                row['trace_failure'] = True
        rows.append(row)
        result = {'function': name, 'variant_group': variant_group,
                  'hypothesis': 'Explicit value materialization intervention; no claim to original source types or qualifiers',
                  'rows': rows, 'scored_candidates': len(rows), 'seconds': time.monotonic() - start,
                  'scope': 'Exposed development follow-up; volatile qualification is an explicit compiler intervention, not recovered source truth',
                  'training_eligible': False, 'imported': False}
        (HERE / (name + '-' + variant_group + '.json')).write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in row.items() if k not in ('diagnosis', 'protocol')}), flush=True)
        if certified:
            (folder / 'materialization-exact.c').write_text(code)
            break
    conn.close()


if __name__ == '__main__':
    main()

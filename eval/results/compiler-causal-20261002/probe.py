"""Bounded, logged causal interventions; no live KB or campaign mutation."""
from pathlib import Path
import hashlib
import itertools
import json
import re
import shutil
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / 'inputs.json').read_text())
WORK = Path(INPUTS['work'])
CODE = Path(INPUTS['code'])
sys.path.insert(0, str(CODE))
from eval import campaign_workers
from solver import regalloc_mutations as rm, regalloc_search as rs, regalloc_signature as sig
from solver import register_protocol, uopt_diagnosis as ud, workspace

REPO = Path('/home/grant/decomp/sbk1')
TRACE_CC = Path('/home/grant/decomp/tools-src/ido-trace/cc')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def certified(attempt):
    return bool(attempt.compiled and (attempt.frontend or {}).get('passed') and
                (attempt.exact or (attempt.verification or {}).get('function_boundary', {}).get('function_exact')))


def direct_interventions(source, function):
    """Explicit candidate-owned hypotheses, frozen before observing their compiles."""
    begin, end = rm._body(source, function)
    body = source[begin:end]
    if function == 'randomNextObject':
        access = '(*(u8 *)((u8 *)(arg0) + 0x518))'
        assert access in body and '&gRandomTable' in body
        for ctype in ('u8', 'u32', 'u16', 's32'):
            proposals = [
                f'\n    {ctype} idx;\n    idx = ({access} += 1);\n    return *(&gRandomTable + (idx & 0xFF));\n',
                f'\n    {ctype} idx;\n    {access} += 1;\n    idx = {access};\n    return *(&gRandomTable + (idx & 0xFF));\n',
                f'\n    {ctype} idx;\n    idx = {access} + 1;\n    {access} = idx;\n    return *((idx & 0xFF) + &gRandomTable);\n',
            ]
            for mode, text in enumerate(proposals):
                yield f'direct:materialization:{ctype}:{mode}', 'direct-materialization', source[:begin] + text + source[end:]
        for vtype, itype in itertools.product(('u8', 'u16', 'u32', 's32'), repeat=2):
            text = body.replace('u8 new_val;', vtype + ' new_val;').replace('u8 idx;', itype + ' idx;')
            yield f'direct:width:{vtype}:{itype}', 'direct-width', source[:begin] + text + source[end:]
    elif function == 'updateEndingCreditsIdleSparkle':
        condition = 'if ((temp_t0 & 0xFFFF) == 5)'
        reread = 'if ((*(u16 *)((unsigned char *)arg0 + 0x1C)) == 5)'
        assert condition in body
        for ctype, readback, explicit in itertools.product(('u16', 'u32', 's32'), (False, True), (False, True)):
            text = body.replace('u16 temp_t0;', ctype + ' temp_t0;')
            if readback:
                text = text.replace(condition, reread)
            if explicit:
                text = text.replace('temp_t0 = (*(u16 *)((unsigned char *)arg0 + 0x1C)) + 1;',
                                    'temp_t0 = (u16)((*(u16 *)((unsigned char *)arg0 + 0x1C)) + 1);')
            yield f'direct:reread:{ctype}:{readback}:{explicit}', 'direct-reread', source[:begin] + text + source[end:]


def run_case(item, conn):
    function = item['function']
    source = Path(item['source']).read_text()
    assert sha(source.encode()) == item['source_sha256']
    directory = WORK / function
    directory.mkdir(exist_ok=False)
    repo = campaign_workers.isolate(REPO, directory / 'repo', function)
    # The helper may normalize its own policy. Keep even that write private.
    (repo / 'tools').unlink()
    shutil.copytree(REPO / 'tools', repo / 'tools', symlinks=True)
    ws = workspace.bootstrap(repo, function)
    target = (ws / 'target_object_dump_normalized.s').read_text()
    index, attempts, trace_events = 0, {}, []
    case_start = time.monotonic()

    def compile_one(code, label, arm, parent=None):
        nonlocal index
        index += 1
        tag = f'causal_{index:04d}'
        start = time.monotonic()
        result = workspace.score(ws, repo, tag, code, conn=conn, func=function,
            strategy=f'compiler-causal:{arm}:{label}', run_id='compiler-causal-20261002',
            parent_attempt_id=parent, relation='causal-intervention',
            extra={'training_eligible': False, 'input_sha256': item['source_sha256'],
                   'assistance': item['assistance'], 'hypothesis_label': label})
        conn.commit()
        dump_path = ws / (tag + '_object_dump_normalized.s')
        dump = dump_path.read_text() if result.compiled and dump_path.exists() else None
        gradient = list(sig.compare(target, dump).gradient) if dump else None
        row = {'label': label, 'arm': arm, 'attempt_id': result.receipt_id, 'parent_attempt_id': parent,
               'source_sha256': sha(code.encode()), 'compiled': result.compiled,
               'frontend_passed': (result.frontend or {}).get('passed'), 'certified': certified(result),
               'object_exact': result.exact,
               'function_exact': (result.verification or {}).get('function_boundary', {}).get('function_exact'),
               'gradient': gradient, 'seconds': time.monotonic() - start, 'tag': tag}
        with (directory / 'attempts.jsonl').open('a') as log:
            log.write(json.dumps(row) + '\n')
        attempts[code] = (result, dump, row)
        evidence = {**(result.verification or {}), 'frontend': result.frontend,
                    'source_attribution': result.source_attribution, 'compiler_recipe': result.compiler_recipe}
        return rs.Compiled(result.compiled, certified(result), dump, result.diff, evidence=evidence)

    baseline = compile_one(source, 'baseline', 'baseline')
    baseline_id = attempts[source][0].receipt_id
    if not baseline.compiled:
        raise RuntimeError(function + ': baseline no longer compiles; do not reinterpret this as a failed lever')

    def trace(code, label, compiled):
        trace_start = time.monotonic()
        calls = []
        original = ud.subprocess.run
        def counted(command, *args, **kwargs):
            started = time.monotonic()
            result = original(command, *args, **kwargs)
            if any(str(part) == str(TRACE_CC) for part in command):
                calls.append({'flags': [x for x in command if str(x).startswith('-W')],
                              'returncode': result.returncode, 'seconds': time.monotonic() - started})
            return result
        ud.subprocess.run = counted
        try:
            texts = ud.traced_compile(ws, repo, code, TRACE_CC, function)
        finally:
            ud.subprocess.run = original
        report = None
        folder = directory / f'trace-{len(trace_events):03d}'
        folder.mkdir()
        if texts:
            for name, text in texts.items():
                (folder / (name + '.txt')).write_text(text)
            (folder / 'source.c').write_text(code)
            (folder / 'candidate.s').write_text(compiled.dump)
            (folder / 'target.s').write_text(target)
            report = ud.diagnose(target, compiled.dump, texts['level5'], texts['level6'], texts['ugen'], function)
            protocol = register_protocol.analyse(target, compiled.dump, texts['level5'], texts['level6'], texts['ugen'], function)
            (folder / 'diagnosis.json').write_text(json.dumps(report, indent=2) + '\n')
            (folder / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
        trace_events.append({'label': label, 'source_sha256': sha(code.encode()), 'calls': calls,
                             'seconds': time.monotonic() - trace_start, 'available': bool(texts),
                             'first': report.get('first') if report else None, 'directory': str(folder)})
        (directory / 'trace-events.json').write_text(json.dumps(trace_events, indent=2) + '\n')
        return report

    initial = trace(source, 'initial-diagnosis', baseline)
    summaries = []
    for arm, budget in [('ordinary', 96), ('diagnosed', 84)]:
        before_count = index
        before_trace = len(trace_events)
        arm_start = time.monotonic()
        def parent_compile(code, label, parent_source):
            parent = attempts.get(parent_source, (None, None, None))[0]
            return compile_one(code, label, arm, parent.receipt_id if parent else baseline_id)
        outcome = rs.search(function, source, lambda code, label: parent_compile(code, label, source), target,
                            budget=budget, beam=3, depth=4, baseline=baseline,
                            compile_with_parent=parent_compile,
                            trace=trace if arm == 'diagnosed' else None,
                            trace_budget=4, coalesce=False)
        rows = {'arm': arm, **outcome.summary(), 'actual_scored_candidates': index - before_count,
                'diagnostic_compiles': sum(len(r['calls']) for r in trace_events[before_trace:]),
                'seconds': time.monotonic() - arm_start, 'best_source_sha256': sha(outcome.best_source.encode())}
        (directory / (arm + '-best.c')).write_text(outcome.best_source)
        (directory / (arm + '-search.json')).write_text(json.dumps({'summary': rows, 'log': outcome.log}, indent=2) + '\n')
        summaries.append(rows)
        print(json.dumps({'function': function, **rows}), flush=True)
    force_start, before_count = time.monotonic(), index
    first = (initial or {}).get('first') or {}
    preferred = ('single_use', 'pure_inline', 'typed_reread', 'field_local', 'readonly_field_local',
                 'self_update', 'compound_assign', 'scalar_coalesce')
    if first.get('desired') not in register_protocol.UGEN_TEMPS:
        preferred = ud.preferred_families(initial)
    pool = itertools.chain(direct_interventions(source, function),
                           rm.variants(source, function, baseline.diff, prefer=preferred,
                                       evidence=baseline.evidence, coalesce=True))
    seen, winner, best_code, best_gradient = {source}, None, source, sig.compare(target, baseline.dump).gradient
    for label, kind, code in pool:
        if index - before_count >= 84:
            break
        if code in seen:
            continue
        seen.add(code)
        result = compile_one(code, label, 'force', baseline_id)
        if result.dump:
            gradient = sig.compare(target, result.dump).gradient
            if gradient < best_gradient:
                best_gradient, best_code = gradient, code
        if result.exact:
            winner, best_code, best_gradient = label, code, sig.compare(target, result.dump).gradient
            break
    force_count = index - before_count
    selected = attempts[best_code]
    if best_code != source:
        trace(best_code, 'force-best-diagnosis', rs.Compiled(selected[0].compiled, certified(selected[0]), selected[1]))
    if winner:
        repeat = compile_one(best_code, 'independent-repeat', 'confirmation', selected[0].receipt_id)
        assert repeat.exact, 'exact proposal did not reproduce'
    (directory / 'force-best.c').write_text(best_code)
    summaries.append({'arm': 'force', 'exact': bool(winner), 'winner': winner,
                      'actual_scored_candidates': force_count, 'best_gradient': list(best_gradient),
                      'seconds': time.monotonic() - force_start, 'best_source_sha256': sha(best_code.encode())})
    result = {'function': function, 'scope': item['scope'], 'baseline_gradient': list(sig.compare(target, baseline.dump).gradient),
              'baseline_diagnosis': initial, 'baseline_attempt_id': baseline_id, 'arms': summaries,
              'scored_candidates_including_baseline_and_repeat': index,
              'diagnostic_compiles': sum(len(r['calls']) for r in trace_events),
              'seconds': time.monotonic() - case_start, 'directory': str(directory),
              'already_exact_at_freeze': item['research_exact_attempts_at_freeze'] > 0 or item['campaign_status_at_freeze'] in ('object_exact', 'integrated'),
              'training_eligible': False, 'imported': False}
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (HERE / (function + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'baseline_diagnosis'}), flush=True)
    return result


def main():
    scope = sys.argv[1] if len(sys.argv) > 1 else 'development'
    assert scope in ('development', 'followup')
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    conn.executescript((CODE / 'kb/schema.sql').read_text())
    if not conn.execute('SELECT count(*) FROM functions').fetchone()[0]:
        conn.execute('ATTACH DATABASE ? AS origin', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
        for table in ('extraction', 'tus', 'functions'):
            conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
        conn.commit()
        conn.execute('DETACH DATABASE origin')
    results = []
    for item in INPUTS['inputs']:
        if item['scope'] != scope:
            continue
        result = run_case(item, conn)
        results.append(result)
        (HERE / (scope + '-results.json')).write_text(json.dumps(results, indent=2) + '\n')
    conn.close()


if __name__ == '__main__':
    main()

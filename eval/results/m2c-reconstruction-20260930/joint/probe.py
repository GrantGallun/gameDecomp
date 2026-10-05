"""Frozen native multi-function m2c DEV trial; private outputs only."""
from pathlib import Path
from dataclasses import asdict
import difflib
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from solver import binary_type_draft as bd, m2c_input, m2c_byte_view, repair_context, workspace, signals
from eval.research_suite.compiler import NativeCompiler, environment

PRIOR = ROOT/'eval/results/joint-reconstruction-20260930'
OUTPUT = Path('/home/grant/decomp/experiments/m2c-joint-native-20260930-v1')
PORTABLE = Path(__file__).parent/'portable'
REPO = Path('/home/grant/decomp/sbk1')
FUNCTIONS = ['drawMenuSprite', 'drawMenuSpriteClipped', 'drawMenuSpriteWithAlpha', 'drawMenuSpriteWithAlphaClipped']
TARGET = 'build/src/menu/renderer/menu_renderer.o'
ARMS = [('alone2', 2), ('group2', 2), ('group4', 4)]

def sha(text):
    return hashlib.sha256(text if isinstance(text, bytes) else text.encode()).hexdigest()

def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2)+'\n')

def invoke(functions, passes, arm, context, assemblies):
    folder = OUTPUT/'generation'/arm
    if len(functions) == 1:
        folder /= functions[0]
    folder.mkdir(parents=True, exist_ok=False)
    inputs = []
    for fn in functions:
        path = folder/(fn+'.s')
        path.write_text(assemblies[fn])
        inputs.append(str(path))
    ctx = folder/'context.c'
    ctx.write_text(context)
    command = [str(REPO/'.venv/bin/m2c'), '--target', 'mips-ido-c', '--no-cache',
               '--context', str(ctx), '--valid-syntax', '--passes', str(passes), *inputs]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=180)
    (folder/'m2c.stdout').write_text(result.stdout)
    (folder/'m2c.stderr').write_text(result.stderr)
    write(folder/'receipt.json', {'command': command, 'returncode': result.returncode,
          'functions': functions, 'passes': passes, 'context_sha256': sha(context),
          'assembly_sha256': {fn: sha(assemblies[fn]) for fn in functions}})
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout, folder

def run():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    census = json.loads((PRIOR/'census.json').read_text())
    assert FUNCTIONS == json.loads((PRIOR/'selection.json').read_text())['functions']
    assert not set(FUNCTIONS) & set(census['heldout'])
    shared = (PRIOR/'joint-context.h').read_text()
    write(OUTPUT/'preregistration.json', {'question': 'Native m2c shared multi-function inference versus identical-context alone inference',
          'functions': FUNCTIONS, 'arms': ARMS, 'compile_budget': 12, 'valid_syntax': True,
          'selection': 'fixed exposed DEV sprite cluster; chosen before this trial outcomes',
          'header': 'identical prior binary-derived shared header plus clean public SDK prelude',
          'lowering': 'unchanged solver.m2c_byte_view.lower separately on each extracted function',
          'training_eligible': False, 'reference_source_used': False, 'probe_sha256': sha(Path(__file__).read_bytes()),
          'scope': 'isolated functions; no full TU compile or semantic claim'})
    shutil.copytree(PRIOR/'portable/targets', OUTPUT/'targets')
    (OUTPUT/'empty-context').mkdir()
    (OUTPUT/'shared-context.h').write_text(shared)
    identity = environment(REPO, [TARGET])
    write(OUTPUT/'environment.json', identity)
    package = Path(__import__('m2c').__file__).parent
    main = (package/'main.py').read_text()
    (OUTPUT/'m2c-main.py').write_text(main)
    direct_url = next(package.parent.glob('m2c-*.dist-info/direct_url.json')).read_text()
    write(OUTPUT/'m2c-identity.json', {'direct_url': json.loads(direct_url),
          'package_sha256': {p.relative_to(package).as_posix(): sha(p.read_bytes()) for p in sorted(package.rglob('*.py'))},
          'shared_inference_evidence': 'One TypePool and GlobalInfo constructed before options.passes - 1 preliminary translations; see saved m2c-main.py'})
    assemblies = {}
    for fn in FUNCTIONS:
        raw = (OUTPUT/'targets'/fn/'target.s').read_text()
        # Feed only each function body, not four repeated macro/register preludes.
        match = re.search(r'(?m)^glabel '+re.escape(fn)+r'\s*$', raw)
        assert match and len(re.findall(r'(?m)^glabel ', raw)) == 1
        body = raw[match.start():]
        assemblies[fn], aliases = m2c_input.normalize_o32_registers(body)
        write(OUTPUT/'targets'/fn/'input.json', {'raw_sha256': sha(raw), 'body_sha256': sha(body),
              'normalized_sha256': sha(assemblies[fn]), 'aliases': aliases})
    pre = OUTPUT/'preprocessing'
    pre.mkdir()
    context, metadata = bd._preprocess(REPO, bd.CLEAN_PRELUDE+shared, pre)
    write(pre/'receipt.json', metadata)
    stdout = {}
    for arm, passes in ARMS:
        if arm == 'alone2':
            for fn in FUNCTIONS:
                stdout[(arm, fn)], _ = invoke([fn], passes, arm, context, assemblies)
        else:
            result, _ = invoke(FUNCTIONS, passes, arm, context, assemblies)
            for fn in FUNCTIONS:
                stdout[(arm, fn)] = result
    candidates = {}
    bodies = {}
    generations = {}
    for arm, _ in ARMS:
        for fn in FUNCTIONS:
            folder = OUTPUT/'drafts'/arm/fn
            folder.mkdir(parents=True)
            report = {'status': 'declined', 'reference_source_used': False}
            try:
                text = stdout[(arm, fn)]
                definition, end = repair_context.definition(text, fn)
                body = text[definition.start():end]
                bodies[(arm, fn)] = body
                (folder/'body-before-lowering.c').write_text(body)
                # Isolate the draft before lowering, identically in every arm.
                # Context declarations are outside m2c's output when already supplied.
                lowered = m2c_byte_view.lower(body, fn, target_assembly=assemblies[fn])
                body = lowered['source'] if isinstance(lowered, dict) else lowered
                own = re.compile(r'(?m)^s32 '+re.escape(fn)+r'\([^\n]*\);\n')
                header, count = own.subn('', shared)
                assert count == 1
                source = bd.CLEAN_PRELUDE+header+'\n'+body+'\n'
                (folder/'source.c').write_text(source)
                candidates[(arm, fn)] = source
                report.update(status='generated', source_sha256=sha(source), body_sha256=sha(body),
                              lowering={k: v for k, v in lowered.items() if k != 'source'})
            except (ValueError, RuntimeError) as exc:
                report['error'] = str(exc)
            generations[(arm, fn)] = report
            write(folder/'generation.json', report)
    controls = {'identical_context': True, 'identical_flags_except_passes_and_file_list': True,
                'heldout_overlap': [], 'copied_target_hashes': {fn: sha((OUTPUT/'targets'/fn/'target.o').read_bytes()) for fn in FUNCTIONS},
                'raw_body_differences': {fn: {} for fn in FUNCTIONS}}
    for fn in FUNCTIONS:
        for arm in ('group2', 'group4'):
            left, right = bodies.get(('alone2', fn), ''), bodies.get((arm, fn), '')
            controls['raw_body_differences'][fn][arm] = left != right
            (OUTPUT/f'{fn}.{arm}.diff').write_text(''.join(difflib.unified_diff(left.splitlines(True), right.splitlines(True), 'alone2', arm)))
    write(OUTPUT/'controls.json', controls)
    conn = sqlite3.connect(OUTPUT/'attempts.sqlite')
    conn.executescript((ROOT/'kb/schema.sql').read_text())
    for fn in FUNCTIONS:
        row = census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)', (row['tu_id'], TARGET))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)', (row['addr'], fn, row['tu_id'], row['insn_count']))
    conn.commit()
    rows = []
    for arm, passes in ARMS:
        for fn in FUNCTIONS:
            row = {'function': fn, 'arm': arm, 'passes': passes, 'generation': generations[(arm, fn)],
                   'compiled': False, 'frontend_passed': False, 'exact': False}
            source = candidates.get((arm, fn))
            if source is not None:
                task = {'function': fn, 'compile_target': TARGET, 'target_object': f'targets/{fn}/target.o', 'context': 'empty-context'}
                compiler = NativeCompiler(REPO, task, OUTPUT, OUTPUT/'compiles'/arm/fn, budget=1, identity=identity)
                result = compiler(source, arm)
                measured = compiler.rows[-1]
                row.update(compiled=result.compiled, exact=result.exact,
                           frontend_passed=(measured.get('frontend') or {}).get('passed') is True,
                           error=measured.get('error'), receipt=str(compiler.output/measured['artifact']/'receipt.json'))
                if result.compiled:
                    row['faults'] = asdict(signals.analyse(result.diff, 0))
                attempt = workspace.Attempt(result.compiled, 0, result.exact, result.diff or '', measured.get('error') or '', '',
                    verification=measured.get('verification'), frontend=measured.get('frontend'), compiler_recipe=compiler.recipe)
                row['attempt_id'] = workspace.record_attempt(conn, fn, source, attempt,
                    strategy='m2c-native-joint-dev:'+arm, run_id=OUTPUT.name, run_kind='dev-spike',
                    wall_ms=int(measured['seconds']*1000), extra={'generation': row['generation'], 'training_eligible': False, 'score_available': False})
                conn.commit()
            else:
                attempt = workspace.Attempt(False, 0, False, '', row['generation'].get('error', 'draft unavailable'), '')
                row['attempt_id'] = workspace.record_attempt(conn, fn, '', attempt, strategy='m2c-native-joint-dev:'+arm,
                    run_id=OUTPUT.name, run_kind='dev-spike', extra={'generation': row['generation'], 'training_eligible': False, 'score_available': False})
                conn.commit()
            rows.append(row)
            write(OUTPUT/'comparison.partial.json', {'rows': rows})
            print(json.dumps({k: row[k] for k in ('function', 'arm', 'compiled', 'frontend_passed', 'exact')}), flush=True)
    conn.close()
    summary = {arm: {key: sorted({r['function'] for r in rows if r['arm'] == arm and r[key]}) for key in ('compiled', 'frontend_passed', 'exact')} for arm, _ in ARMS}
    write(OUTPUT/'comparison.json', {'summary': summary, 'rows': rows, 'controls': controls,
          'deployed': False, 'production_kb_modified': False, 'model_calls': 0, 'full_TU_checked': False})
    shutil.copytree(OUTPUT, PORTABLE)
    print(json.dumps(summary), flush=True)

if __name__ == '__main__':
    run()

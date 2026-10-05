"""Exposed exploratory native m2c trial without prior layout/signature context."""
from pathlib import Path
from dataclasses import asdict
import difflib
import json
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import probe as p
from solver import binary_type_draft as bd, m2c_input, m2c_byte_view, repair_context, workspace, signals
from eval.research_suite.compiler import NativeCompiler, environment

OUT = p.OUTPUT/'secondary'

def run():
    OUT.mkdir(exist_ok=False)
    p.write(OUT/'preregistration.json', {'kind': 'exposed exploratory secondary', 'functions': p.FUNCTIONS,
        'arms': ['alone2', 'group2'], 'passes': 2, 'additional_compile_budget': 8, 'total_campaign_budget': 20,
        'context': 'public clean prelude only; no prior sprite prototypes, globals or layouts',
        'valid_syntax': True, 'lowering': 'existing m2c_byte_view.lower, identical in both arms',
        'compile_input': 'retain emitted types/global declarations plus generated helper signatures when isolating grouped definitions',
        'reference_source_used': False, 'training_eligible': False, 'probe_sha256': p.sha(Path(__file__).read_bytes()),
        'prior_result_known': 'shared binary-context alone/group drafts identical; 2/4 frontend compile, 0/4 exact',
        'scope': 'isolated functions; no full TU compile or runtime validation'})
    shutil.copytree(p.OUTPUT/'targets', OUT/'targets')
    (OUT/'empty-context').mkdir()
    identity = environment(p.REPO, [p.TARGET])
    p.write(OUT/'environment.json', identity)
    pre = OUT/'preprocessing'
    pre.mkdir()
    context, metadata = bd._preprocess(p.REPO, bd.CLEAN_PRELUDE, pre)
    p.write(pre/'receipt.json', metadata)
    stdout = {}
    for arm in ('alone2', 'group2'):
        groups = [[fn] for fn in p.FUNCTIONS] if arm == 'alone2' else [p.FUNCTIONS]
        for fns in groups:
            folder = OUT/'generation'/arm
            if len(fns) == 1:
                folder /= fns[0]
            folder.mkdir(parents=True)
            inputs = []
            for fn in fns:
                path = folder/(fn+'.s')
                raw = (OUT/'targets'/fn/'target.s').read_text()
                import re
                begin = re.search(r'(?m)^glabel '+fn+r'\s*$', raw).start()
                assembly, _ = m2c_input.normalize_o32_registers(raw[begin:])
                path.write_text(assembly)
                inputs.append(str(path))
            ctx = folder/'context.c'
            ctx.write_text(context)
            command = [str(p.REPO/'.venv/bin/m2c'), '--target', 'mips-ido-c', '--no-cache', '--context', str(ctx),
                       '--valid-syntax', '--passes', '2', *inputs]
            result = subprocess.run(command, cwd=p.REPO, capture_output=True, text=True, timeout=180)
            (folder/'m2c.stdout').write_text(result.stdout)
            (folder/'m2c.stderr').write_text(result.stderr)
            p.write(folder/'receipt.json', {'command': command, 'returncode': result.returncode,
                'functions': fns, 'passes': 2, 'context_sha256': p.sha(context)})
            for fn in fns:
                stdout[(arm, fn)] = result.stdout
    census = json.loads((p.PRIOR/'census.json').read_text())
    conn = sqlite3.connect(OUT/'attempts.sqlite')
    conn.executescript((p.ROOT/'kb/schema.sql').read_text())
    for fn in p.FUNCTIONS:
        item = census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)', (item['tu_id'], p.TARGET))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)', (item['addr'], fn, item['tu_id'], item['insn_count']))
    conn.commit()
    rows, bodies, signatures = [], {}, {}
    for arm in ('alone2', 'group2'):
        for fn in p.FUNCTIONS:
            folder = OUT/'drafts'/arm/fn
            folder.mkdir(parents=True)
            text = stdout[(arm, fn)]
            generation = {'reference_source_used': False}
            try:
                definitions = {}
                for name in p.FUNCTIONS:
                    try:
                        match, end = repair_context.definition(text, name)
                        definitions[name] = (match, end)
                    except ValueError:
                        continue
                match, end = definitions[fn]
                body = text[match.start():end]
                bodies[(arm, fn)] = body
                signatures[(arm, fn)] = text[match.start():match.end()-1].strip()
                (folder/'body-before-lowering.c').write_text(body)
                declarations = text
                for item, stop in sorted(definitions.values(), key=lambda x: x[0].start(), reverse=True):
                    declarations = declarations[:item.start()]+declarations[stop:]
                # The grouped stdout normally omits prototypes for functions it defines.
                # Preserve the generated ABI for isolated helper calls, not a prior guess.
                helpers = '\n'.join(text[m.start():m.end()-1].strip()+';' for name, (m, _) in definitions.items() if name != fn)
                source = bd.CLEAN_PRELUDE+'\n'+declarations+'\n'+helpers+'\n'+body+'\n'
                (folder/'raw-source.c').write_text(source)
                assembly = (OUT/'generation'/arm/(fn if arm == 'alone2' else '')/(fn+'.s')).read_text()
                try:
                    lowered = m2c_byte_view.lower(source, fn, target_assembly=assembly)
                    source = lowered['source']
                    generation['lowering'] = {k: v for k, v in lowered.items() if k != 'source'}
                except (ValueError, RuntimeError) as exc:
                    generation['lowering_declined'] = str(exc)
                    generation['raw_source_compiled_without_lowering'] = True
                generation.update(status='generated', source_sha256=p.sha(source), signature=signatures[(arm, fn)])
            except (ValueError, RuntimeError, KeyError) as exc:
                source = bd.CLEAN_PRELUDE+text
                generation.update(status='extraction_failed_raw_output_retained', error=str(exc))
            (folder/'source.c').write_text(source)
            p.write(folder/'generation.json', generation)
            task = {'function': fn, 'compile_target': p.TARGET, 'target_object': f'targets/{fn}/target.o', 'context': 'empty-context'}
            compiler = NativeCompiler(p.REPO, task, OUT, OUT/'compiles'/arm/fn, budget=1, identity=identity)
            result = compiler(source, arm)
            measured = compiler.rows[-1]
            row = {'function': fn, 'arm': arm, 'generation': generation, 'compiled': result.compiled,
                   'exact': result.exact, 'frontend_passed': (measured.get('frontend') or {}).get('passed') is True,
                   'error': measured.get('error'), 'receipt': str(compiler.output/measured['artifact']/'receipt.json')}
            if result.compiled:
                row['faults'] = asdict(signals.analyse(result.diff, 0))
            attempt = workspace.Attempt(result.compiled, 0, result.exact, result.diff or '', measured.get('error') or '', '',
                verification=measured.get('verification'), frontend=measured.get('frontend'), compiler_recipe=compiler.recipe)
            row['attempt_id'] = workspace.record_attempt(conn, fn, source, attempt, strategy='m2c-native-joint-exploratory:'+arm,
                run_id=OUT.name, run_kind='dev-spike', wall_ms=int(measured['seconds']*1000),
                extra={'generation': generation, 'training_eligible': False, 'score_available': False})
            conn.commit()
            rows.append(row)
            p.write(OUT/'comparison.partial.json', {'rows': rows})
            print(json.dumps({k: row[k] for k in ('function', 'arm', 'compiled', 'frontend_passed', 'exact')}), flush=True)
    conn.close()
    differences = {}
    for fn in p.FUNCTIONS:
        left, right = bodies.get(('alone2', fn), ''), bodies.get(('group2', fn), '')
        (OUT/(fn+'.diff')).write_text(''.join(difflib.unified_diff(left.splitlines(True), right.splitlines(True), 'alone2', 'group2')))
        differences[fn] = {'body_changed': left != right, 'signatures': {arm: signatures.get((arm, fn)) for arm in ('alone2', 'group2')}}
    summary = {arm: {key: sorted({r['function'] for r in rows if r['arm'] == arm and r[key]}) for key in ('compiled', 'frontend_passed', 'exact')} for arm in ('alone2', 'group2')}
    p.write(OUT/'comparison.json', {'summary': summary, 'rows': rows, 'differences': differences,
        'deployed': False, 'production_kb_modified': False, 'model_calls': 0, 'full_TU_checked': False})
    shutil.copytree(OUT, p.PORTABLE/'secondary')
    print(json.dumps(summary), flush=True)

if __name__ == '__main__':
    run()

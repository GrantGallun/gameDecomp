"""Frozen SBK1-only signed-counter development probe, never a transfer claim."""
from pathlib import Path
import hashlib
import itertools
import json
import shutil
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/ido-signed-counter-20261002')
OLD = Path('/home/grant/decomp/experiments/compiler-causal-20261002')
NAMES = ('updateEndingSlashVanishBeforeExitRight', 'updateEndingSlashLongWaitSetShadow')
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

def proposals(source, name):
    access = '(*(u16 *)((u8 *)(arg0) + 0x2A))'
    result = 'temp_v0' if name == NAMES[0] else 'var_v0'
    indent = '        ' if name == NAMES[0] else '    '
    sequence = (f'{indent}temp_t7 = {access} + 1;\n'
                f'{indent}{result} = temp_t7 & 0xFFFF;\n'
                f'{indent}{access} = temp_t7;')
    assert source.count(sequence) == 1
    for label, update in (('compound', f'{access} += 1;'),
                          ('postincrement', f'{access}++;'),
                          ('assignment', f'{access} = {access} + 1;')):
        text = source.replace(sequence, f'{indent}{update}\n{indent}{result} = {access};')
        text = text.replace('    s16 temp_t7;\n', '')
        yield label + ':retained-comparison-local', text
    text = source.replace(sequence, f'{indent}{access}++;')
    text = text.replace('    s16 temp_t7;\n', '').replace(f'    s32 {result};\n', '')
    if name == NAMES[1]:
        text = text.replace(f'        {result} = {access};\n', '')
    text = text.replace(result, access)
    yield 'postincrement:comparison-rereads', text

def main():
    assert sys.platform == 'linux'
    WORK.mkdir(parents=True, exist_ok=False)
    code = WORK / 'code'
    for folder in ('solver', 'kb', 'miner', 'patterns', 'eval'):
        shutil.copytree(PROJECT / folder, code / folder,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', '*.sqlite', '*.pyc'))
    sys.path.insert(0, str(code))
    from eval import campaign_workers
    from solver import workspace, regalloc_mutations as rm, regalloc_signature as sig
    from solver import uopt_diagnosis as ud, byte_certificate
    plans = []
    for name in NAMES:
        source = (OLD / 'inputs' / (name + '.c')).read_text()
        root = WORK / 'inputs' / (name + '.c')
        root.parent.mkdir(exist_ok=True)
        root.write_text(source)
        variants = []
        for i, (label, text) in enumerate(proposals(source, name)):
            path = root.parent / f'{name}-{i}.c'
            path.write_text(text)
            variants.append({'label': label, 'source': str(path), 'sha256': sha(path)})
        plans.append({'function': name, 'root': str(root), 'root_sha256': sha(root), 'variants': variants})
    protocol = {'game': 'sbk1', 'compiler': 'existing SBK1 IDO only',
        'hypothesis': 'Removing the signed narrow increment temporary and restoring the comparison value from the unsigned store will eliminate the earliest wrongly coloured load range while preserving instructions.',
        'prediction': 'The load/add expression will use the target t6/t7 temporaries and the masked comparison value will use v0; ordinary source-bound objects, not trace wording, decide exactness.',
        'panel': 'Two previously exposed development roots, no reference bodies or SBK2 inputs',
        'ordinary_children_per_case': 12, 'targeted_variants_per_case': 4,
        'stop_rule': 'Stop each arm at first exact; one independent scored repeat; no variants added after results.',
        'limits': 'This is a narrow causal probe with unequal finite proposal counts, not a clean equal-budget transfer result.',
        'diagnostics': 'Existing source-bound baseline diagnostics; capture three IDO diagnostic compiles only for a reproduced targeted exact. Compare traced objects to the normal object; discard ugen-debug object.',
        'training_eligible': False, 'campaign_imported': False, 'plans': plans,
        'runner_sha256': sha(__file__), 'code_pins': {str(p.relative_to(code)): sha(p) for p in code.rglob('*') if p.is_file()}}
    (WORK / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    (HERE / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    conn.executescript((code / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS origin', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
    for table in ('tus', 'functions', 'extraction'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE origin')
    results, diagnostic_calls = [], []
    for plan in plans:
        name = plan['function']
        source = Path(plan['root']).read_text()
        folder = WORK / name
        repo = campaign_workers.isolate(OLD / name / 'repo', folder / 'repo', name)
        ws = repo / 'nonmatchings' / name
        target = (ws / 'target_object_dump_normalized.s').read_text()
        rows, tags = [], {}
        def score(text, label, arm, parent=None):
            tag = f'probe_{len(rows):03d}'
            start = time.monotonic()
            att = workspace.score(ws, repo, tag, text, conn=conn, func=name,
                strategy=f'ido-signed-counter:{arm}:{label}', run_id='ido-signed-counter-20261002',
                parent_attempt_id=parent, relation='candidate-construction',
                extra={'training_eligible': False, 'scope': 'exposed-development', 'arm': arm,
                       'hypothesis': protocol['hypothesis'], 'root_sha256': plan['root_sha256']})
            dump_path = ws / (tag + '_object_dump_normalized.s')
            dump = dump_path.read_text() if att.compiled and dump_path.exists() else None
            row = {'label': label, 'arm': arm, 'attempt_id': att.receipt_id,
                   'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                   'compiled': att.compiled, 'frontend_passed': (att.frontend or {}).get('passed'),
                   'object_exact': att.exact, 'accepted': workspace.repair_complete(att),
                   'gradient': list(sig.compare(target, dump).gradient) if dump else None,
                   'seconds': time.monotonic() - start}
            rows.append(row)
            tags[att.receipt_id] = tag
            (folder / 'attempts.json').write_text(json.dumps(rows, indent=2) + '\n')
            return att, dump, row
        baseline, dump, base_row = score(source, 'baseline', 'baseline')
        assert baseline.compiled and not baseline.exact
        ordinary_seen = {source}
        ordinary = []
        for label, kind, text in rm.variants(source, name, baseline.diff,
                evidence={'compiler_recipe': baseline.compiler_recipe, 'source_attribution': baseline.source_attribution}, coalesce=True):
            if text in ordinary_seen:
                continue
            ordinary_seen.add(text)
            att, _, row = score(text, label, 'ordinary', baseline.receipt_id)
            ordinary.append(row)
            if workspace.repair_complete(att) or len(ordinary) == 12:
                break
        targeted, winner = [], None
        for variant in plan['variants']:
            path = Path(variant['source'])
            assert sha(path) == variant['sha256']
            text = path.read_text()
            att, child_dump, row = score(text, variant['label'], 'targeted', baseline.receipt_id)
            targeted.append(row)
            if workspace.repair_complete(att):
                repeated, _, repeat_row = score(text, 'independent-repeat', 'confirmation', att.receipt_id)
                assert workspace.repair_complete(repeated)
                winner = {'variant': variant, 'attempt': row, 'repeat': repeat_row}
                # Diagnostic output is candidate-only and cannot replace acceptance.
                trace_cc = Path('/home/grant/decomp/tools-src/ido-trace/cc')
                real_run = ud.subprocess.run
                traced_images = []
                def recorded(command, *args, **kwargs):
                    started = time.monotonic()
                    try:
                        proc = real_run(command, *args, **kwargs)
                    except Exception as exc:
                        if str(trace_cc) in command:
                            diagnostic_calls.append({'function': name, 'command': command, 'error': str(exc)})
                        raise
                    if str(trace_cc) in command:
                        record = {'function': name, 'command': command, 'returncode': proc.returncode,
                                  'seconds': time.monotonic() - started}
                        if proc.returncode == 0 and '-o' in command and not any(str(x).startswith('-Wc') for x in command):
                            obj = Path(command[command.index('-o') + 1])
                            image = byte_certificate.object_image(obj.read_bytes())
                            equal = image == byte_certificate.object_image((ws / (tags[att.receipt_id] + '.o')).read_bytes())
                            record['ordinary_object_image_equal'] = equal
                            traced_images.append(equal)
                        diagnostic_calls.append(record)
                    return proc
                ud.subprocess.run = recorded
                try:
                    texts = ud.traced_compile(ws, repo, (ws / (tags[att.receipt_id] + '.c')).read_text(), trace_cc, name)
                finally:
                    ud.subprocess.run = real_run
                if texts:
                    diagnostic = ud.diagnose(target, child_dump, texts['level5'], texts['level6'], texts['ugen'], name)
                    traces = folder / 'winner-diagnostics'
                    traces.mkdir()
                    for label, value in texts.items():
                        (traces / (label + '.txt')).write_text(value)
                    winner['diagnosis'] = diagnostic
                    winner['trace_object_correspondence'] = len(traced_images) == 2 and all(traced_images)
                break
        result = {'function': name, 'scope': 'exposed-development', 'baseline': base_row,
                  'ordinary': ordinary, 'targeted': targeted, 'winner': winner}
        results.append(result)
        (HERE / 'results.json').write_text(json.dumps({'cases': results, 'diagnostic_calls': diagnostic_calls,
            'attempts': conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],
            'new_clean_transfer_exacts': 0, 'training_eligible': False, 'campaign_imported': False}, indent=2) + '\n')
        print(json.dumps({'function': name, 'ordinary_exact': any(r['accepted'] for r in ordinary),
                          'targeted_exact': winner is not None, 'targeted_attempts': len(targeted)}), flush=True)
    conn.close()

main()

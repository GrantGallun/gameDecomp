"""Passive observation through actual campaign intake on a frozen panel.

Only process-local generation/bootstrap wrappers are installed. Production
campaign code, solver generators, game workspaces and KB are untouched.
"""
from pathlib import Path
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADAPTER = ROOT / 'eval/results/m2c-reconstruction-20260930'
sys.path[:0] = [str(ROOT), str(ADAPTER)]
from solver.m2c_source_binding import SourceBoundObserver as Observer
from provenance import ProvenanceCollector
from m2c import main
from eval import completion_campaign as campaign
from solver import binary_type_draft as bd, workspace, byte_certificate


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


@contextlib.contextmanager
def patches(changes):
    saved = [(owner, name, getattr(owner, name)) for owner, name, _ in changes]
    try:
        for owner, name, value in changes:
            setattr(owner, name, value)
        yield
    finally:
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


def run(repo, parent, output):
    output.mkdir(parents=True, exist_ok=False)
    selection = json.loads((parent / 'selection.json').read_text())
    functions = selection['functions']
    census = json.loads((ROOT / 'eval/results/joint-reconstruction-20260930/census.json').read_text())
    assert not set(functions) & set(census['heldout'])
    sources = [Path(__file__), HERE / 'panel.py', ADAPTER / 'byte_address.py', ADAPTER / 'provenance.py',
               ROOT / 'eval/completion_campaign.py', ROOT / 'solver/binary_type_draft.py',
               ROOT / 'solver/workspace.py', ROOT / 'solver/compile_fallback.py', ROOT/'solver/m2c_uncertainty.py', ROOT/'solver/m2c_source_binding.py']
    code = output / 'code'
    code.mkdir()
    pins = {}
    for source in sources:
        relative = str(source.relative_to(ROOT))
        pins[relative] = sha(source.read_bytes())
        destination = code / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    write(output / 'preregistration.json', {
        'kind': 'throwaway-campaign-intake-connection', 'functions': functions,
        'arms': ['current'], 'selection_reused_without_outcome_filtering': True,
        'global_compile_budget': 2048, 'per_function_per_arm_compile_budget': 16,
        'sample_selection': selection,
        'question': 'Can ordinary m2c lifting uncertainty be observed passively and mapped into actual selected campaign seeds?',
        'primary_measure': 'selected source compiled and strict frontend passed',
        'secondary_measures': ['retained valid frontier', 'compiler calls', 'ordinary source retention', 'witness lineage'],
        'model_calls': 0, 'repair_steps_after_intake': 0, 'production_wiring': False,
        'training_eligible': False, 'code_sha256': pins, 'heldout_overlap': []})
    shutil.copytree(parent / 'targets', output / 'targets')
    shutil.copy2(parent / 'selection.json', output / 'selection.json')
    targets = {fn: json.loads((parent / 'compiles' / fn / 'baseline/attempt-00001/receipt.json').read_text())['compile_target']
               for fn in functions}
    db = output / 'attempts.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / 'kb/schema.sql').read_text())
        for fn in functions:
            item = census['metadata'][fn]
            conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)', (item['tu_id'], targets[fn]))
            conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)',
                         (item['addr'], fn, item['tu_id'], item['insn_count']))
    original_variants, original_score, original_m2c = bd.variants, workspace.score, bd._m2c
    rows, attempts = [], []
    for fn in functions:
        for arm in ('current',):
            folder = output / 'intake' / fn / arm
            folder.mkdir(parents=True)
            shutil.copy2(output / 'targets' / fn / 'target.s', folder / 'target.s')
            shutil.copy2(output / 'targets' / fn / 'target.o', folder / 'target.o')
            helper = repo / 'tools/claude-decomp-env'
            for name in ('objdump.py', 'dist.py', 'normalize_asm.py', 'prelude.inc'):
                shutil.copy2(helper / name, folder / name)
            text = (helper / 'build.sh').read_text()
            anchor = 'PROJECT_ROOT="$(cd "$SCRIPT_PATH/../.." && pwd)"'
            assert text.count(anchor) == 1
            text = text.replace(anchor, 'PROJECT_ROOT=' + str(repo))
            (folder / 'build.sh').write_text(text)
            generated_calls, arm_calls = [], []
            arm_started = time.monotonic()
            generation_seconds = 0.0

            def byte_m2c(repo_arg, assembly, context, scratch, *, valid_syntax):
                translation_started = time.monotonic()
                target, ctx = scratch / 'target.s', scratch / 'context.c'
                target.write_text(assembly)
                ctx.write_text(context)
                argv = ['--target', 'mips-ido-c', '--no-cache', '--context', str(ctx)]
                if valid_syntax:
                    argv.append('--valid-syntax')
                argv.append(str(target))
                stdout, stderr = io.StringIO(), io.StringIO()
                with Observer(source_files=[target]) as observer, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    rc = main.run(main.parse_flags(argv))
                index = len(generated_calls)
                observation = folder / 'generation' / str(index)
                try:
                    report = observer.verified_report(stdout.getvalue(), folder/'target.o', function=fn, function_address=census['metadata'][fn]['addr'])
                except (ValueError,OSError) as exc:
                    report=observer.report(stdout.getvalue())
                    report.update(status='partial',binary_binding_error=str(exc))
                    for hazard in report['hazards']:
                        if hazard.get('instruction'):
                            hazard['instruction'].pop('word',None)
                            hazard['instruction'].pop('address',None)
                write(observation / 'uncertainty.json', report)
                (observation/'context.c').write_text(context)
                (observation/'input.s').write_text(assembly)
                normal = original_m2c(repo_arg, assembly, context, scratch, valid_syntax=valid_syntax)
                assert normal.returncode == rc and (rc != 0 or normal.stdout == stdout.getvalue()), 'observer changed ordinary C'
                report['ordinary_stdout_equal'] = rc == 0 and normal.stdout == stdout.getvalue()
                meta = {'valid_syntax': valid_syntax, 'preprocessed_sha256': sha(context.encode()),
                        'normalized_assembly_sha256': sha(assembly.encode()),
                        'byte_address_changes': [], 'late_pointer_store_views': [], 'uncertainty':report,
                        'provenance_path': str(observation / 'uncertainty.json'), 'returncode': rc,
                        'stdout_sha256': sha(stdout.getvalue().encode()),
                        'seconds': time.monotonic() - translation_started}
                write(observation / 'receipt.json', meta)
                (observation / 'm2c.stdout').write_text(stdout.getvalue())
                generated_calls.append(meta)
                return subprocess.CompletedProcess(argv, rc, stdout.getvalue(), stderr.getvalue())

            def connected_variants(repo_arg, function, ws):
                nonlocal generation_seconds
                generation_started = time.monotonic()
                with patches([(bd, '_m2c', byte_m2c)]):
                    ordinary, reports = original_variants(repo_arg, function, ws)
                baseline = {r['label']: r for r in reports if r.get('source_sha256')}
                ordinary_hashes = {sha(source.encode()) for _, source in ordinary}
                for report in reports:
                    report['observer_calls'] = [m for m in generated_calls if m['valid_syntax'] == report.get('valid_syntax') and m['preprocessed_sha256'] == report.get('preprocessing',{}).get('preprocessed_sha256')]
                    if report.get('derived_from_sha256'):
                        assert report['derived_from_sha256'] != report['source_sha256']
                write(folder / 'generation.json', reports)
                write(folder / 'candidate-manifest.json', [{'label': label, 'source_sha256': sha(source.encode())}
                    for label, source in ordinary])
                generation_seconds = time.monotonic() - generation_started
                return ordinary, reports

            def real_score(ws, repo_arg, tag, source, **kwargs):
                if len(attempts) >= 2048 or len(arm_calls) >= 16:
                    raise RuntimeError('preregistered compile budget exhausted')
                metadata = {**(kwargs.pop('extra', None) or {}), 'training_eligible': False,
                            'connection_probe': arm, 'production_wiring': False}
                started = time.monotonic()
                att = original_score(ws, repo_arg, tag, source, **kwargs, extra=metadata,
                                     run_kind='dev-spike')
                row = {'function': fn, 'arm': arm, 'attempt_id': att.receipt_id,
                       'source_sha256': sha(source.encode()), 'compiled': att.compiled,
                       'frontend_passed': (att.frontend or {}).get('passed') is True,
                       'exact': workspace.repair_complete(att), 'score': att.score,
                       'strategy': kwargs['strategy'], 'parent_attempt_id': kwargs.get('parent_attempt_id'),
                       'source_path': str(ws / (tag + '.c')), 'object_path': str(ws / (tag + '.o')),
                       'target_sha256': sha((ws / 'target.o').read_bytes()),
                       'wall_seconds': time.monotonic() - started}
                if att.compiled:
                    row['certificate'] = byte_certificate.certify(ws / 'target.o', ws / (tag + '.o'), source=source)
                write(folder / (tag + '.connection-receipt.json'), row)
                attempts.append(row)
                arm_calls.append(row)
                write(output / 'attempts.json', attempts)
                print(json.dumps({k: row[k] for k in ('function', 'arm', 'attempt_id', 'compiled', 'frontend_passed')}), flush=True)
                return att

            def forbidden(*args, **kwargs):
                raise AssertionError('assisted/model path used in binary-only connection probe')

            with patches([(workspace, 'bootstrap', lambda *args: folder),
                          (workspace, 'score', real_score), (bd, 'variants', connected_variants),
                          (campaign.m2c_context, 'seed_variants', forbidden),
                          (campaign.project_headers, 'preflight_variants', forbidden),
                          (campaign.llm, 'generate', forbidden)]):
                try:
                    result = campaign._intake(repo=repo, db=db, function=fn, node={},
                        out=folder / 'result.json', binary_type_only=True)
                except Exception as exc:
                    import traceback
                    result = {'status':'probe-incomplete', 'error':str(exc),
                              'error_type':type(exc).__name__, 'traceback':traceback.format_exc()}
            write(folder / 'result.json', result)
            chosen = next((a for a in arm_calls if a['attempt_id'] == result.get('attempt_id')), None)
            row = {'function': fn, 'arm': arm, 'compiler_calls': len(arm_calls),
                   'completion_status':result['status'], 'error':result.get('error'),
                   'sample_group': selection['groups'][fn],
                   'total_seconds':time.monotonic()-arm_started, 'generation_seconds':generation_seconds,
                   'selected_attempt_id': result.get('attempt_id'), 'source_sha256': result.get('source_sha256'),
                   'usable_seed': bool(chosen and chosen['compiled'] and chosen['frontend_passed']),
                   'exact': result.get('exact', False), 'frontier': result.get('frontier', []),
                   'generation_calls_with_byte_changes': sum(bool(m['byte_address_changes'] or m['late_pointer_store_views']) for m in generated_calls)}
            rows.append(row)
            write(output / 'comparison.partial.json', {'rows': rows, 'attempts': len(attempts)})
            print(json.dumps(row), flush=True)
    for source in sources:
        relative = str(source.relative_to(ROOT))
        assert source.read_bytes().replace(b'\r\n',b'\n') == (code/relative).read_bytes().replace(b'\r\n',b'\n'), 'code changed during probe'
    result = {'rows': rows, 'compiler_calls': len(attempts), 'production_wiring': False,
              'model_calls': 0, 'training_eligible': False}
    write(output / 'comparison.json', result)
    print(json.dumps({'compiler_calls': len(attempts), 'usable_seeds': {
        arm: sum(r['usable_seed'] for r in rows if r['arm'] == arm) for arm in ('current',)}}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.repo, args.parent, args.output)

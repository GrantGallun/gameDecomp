"""Fresh SBK1 development baselines and source-bound IDO range observations."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/sbk1-dev-bottlenecks-20261002')
CODE = WORK / 'code'
sys.path.insert(0, str(PROJECT))
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

def worker(plan):
    from eval import campaign_workers
    from solver import workspace, regalloc_signature as sig, signals, uopt_diagnosis as ud, byte_certificate
    name = plan['function']
    source = Path(plan['source']).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == plan['start']['source_sha256']
    folder = WORK / 'native' / name
    folder.mkdir(parents=True, exist_ok=False)
    conn = sqlite3.connect(folder / 'trial.sqlite')
    conn.executescript((CODE / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS origin', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
    for table in ('tus', 'functions', 'extraction'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE origin')
    result = {'function': name, 'tu': plan['tu'], 'source_sha256': plan['start']['source_sha256'],
              'database': str(folder / 'trial.sqlite'), 'diagnostic_calls': []}
    started = time.monotonic()
    try:
        repo = campaign_workers.isolate('/home/grant/decomp/sbk1', folder / 'repo', name)
        ws = repo / 'nonmatchings' / name
        # Existing targets only: no bootstrap helper source reconstruction.
        assert (ws / 'target.s').is_file() and (ws / 'target.o').is_file()
        target = (ws / 'target_object_dump_normalized.s').read_text()
        att = workspace.score(ws, repo, 'baseline', source, conn=conn, func=name,
            strategy='dev-bottleneck:baseline', run_id='sbk1-dev-bottleneck-20261002',
            extra={'training_eligible': False, 'scope': 'exposed-development',
                   'pinned_start': plan['start']})
        result.update(attempt_id=att.receipt_id, compiled=att.compiled, exact=att.exact,
                      frontend_passed=(att.frontend or {}).get('passed'), score=att.score,
                      object_discrepancy=att.object)
        if att.compiled:
            dump = (ws / 'baseline_object_dump_normalized.s').read_text()
            report = sig.compare(target, dump)
            result.update(gradient=list(report.gradient), reordered=report.reordered, renames=report.renames,
                          register_signatures=dict(report.signatures), signals=signals.analyse(att.diff, att.score, att.exact, True).__dict__)
            # Trace only a shape-equal, non-exact object. Mixed streams need
            # structural intervention first; no guess from partial range votes.
            if report.non_register == 0 and not att.exact:
                trace_cc = Path('/home/grant/decomp/tools-src/ido-trace/cc')
                real_run = ud.subprocess.run
                def recorded(command, *args, **kwargs):
                    start = time.monotonic()
                    proc = real_run(command, *args, **kwargs)
                    if str(trace_cc) in command:
                        call = {'returncode': proc.returncode, 'seconds': time.monotonic() - start,
                                'command': command}
                        if proc.returncode == 0 and not any(str(x).startswith('-Wc') for x in command):
                            obj = Path(command[command.index('-o') + 1])
                            call['ordinary_object_image_equal'] = byte_certificate.object_image(obj.read_bytes()) == byte_certificate.object_image((ws / 'baseline.o').read_bytes())
                        result['diagnostic_calls'].append(call)
                    return proc
                ud.subprocess.run = recorded
                try:
                    traces = ud.traced_compile(ws, repo, (ws / 'baseline.c').read_text(), trace_cc, name)
                finally:
                    ud.subprocess.run = real_run
                if traces:
                    trace_folder = folder / 'diagnostics'
                    trace_folder.mkdir()
                    for label, text in traces.items():
                        (trace_folder / (label + '.txt')).write_text(text)
                    result['diagnosis'] = ud.diagnose(target, dump, traces['level5'], traces['level6'], traces['ugen'], name)
                    images = [c['ordinary_object_image_equal'] for c in result['diagnostic_calls'] if 'ordinary_object_image_equal' in c]
                    result['trace_object_correspondence'] = len(images) == 2 and all(images)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['seconds'] = time.monotonic() - started
        (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        conn.close()
    return result

def main():
    assert sys.platform == 'linux'
    census = json.loads((HERE / 'census.json').read_text())
    from eval import seal
    manifest = json.loads((PROJECT / 'eval/sets/sbk1_v5_sealed_nearmiss.json').read_text())
    assert manifest['manifest_digest'] == census['manifest_digest']
    seal.assert_dev_only([r['function'] for r in census['rows']], manifest)
    assert not {r['tu'] for r in census['rows']} & seal.sealed_tus_in_sets()
    CODE.mkdir(exist_ok=False)
    for folder in ('solver', 'kb', 'eval', 'miner', 'patterns'):
        shutil.copytree(PROJECT / folder, CODE / folder,
            ignore=shutil.ignore_patterns('results', 'experiments', 'sets', '__pycache__', '*.sqlite', '*.pyc', '*.c', '*.o'))
    sys.path.insert(0, str(CODE))
    workers = min(4, len(os.sched_getaffinity(0)))
    protocol = {'manifest_digest': census['manifest_digest'], 'split': 'dev',
        'functions': [r['function'] for r in census['rows']], 'workers': workers,
        'nproc': os.cpu_count(), 'training_eligible': False, 'campaign_imported': False,
        'plan': 'one fresh baseline per pinned dev source; trace only shape-equal non-exacts; no source edits',
        'runner_sha256': sha(__file__), 'code_pins': {str(p.relative_to(CODE)): sha(p) for p in CODE.rglob('*') if p.is_file()}}
    helper = Path('/home/grant/decomp/sbk1/tools/claude-decomp-env/build.sh').read_text()
    from solver import workspace
    assert not workspace.DO_BAN.search(helper), 'refuse shared helper mutation'
    (HERE / 'native-protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    results = []
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker, r) for r in census['rows']]
        for future in as_completed(futures):
            r = future.result()
            results.append(r)
            out = {'rows': sorted(results, key=lambda r: r['function']), 'seconds': time.monotonic() - start}
            (HERE / 'native-results.json').write_text(json.dumps(out, indent=2) + '\n')
            if len(results) % 10 == 0 or r.get('error'):
                print(json.dumps({'completed': len(results), 'errors': sum('error' in x for x in results),
                    'shape_equal': sum(x.get('gradient', [1])[0] == 0 for x in results)}), flush=True)
    classes = Counter(r.get('diagnosis', {}).get('first', {}).get('class', 'none') if r.get('diagnosis', {}).get('first') else 'none' for r in results if 'diagnosis' in r)
    print(json.dumps({'completed': len(results), 'errors': sum('error' in r for r in results),
        'compiled': sum(r.get('compiled', False) for r in results), 'exact': sum(r.get('exact', False) for r in results),
        'shape_equal': sum(r.get('gradient', [1])[0] == 0 for r in results), 'first_range_classes': dict(classes),
        'seconds': time.monotonic() - start}), flush=True)

if __name__ == '__main__':
    main()

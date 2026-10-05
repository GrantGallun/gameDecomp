"""Freeze first, then run a private equal-budget development comparison in WSL.

No target source answers enter candidate construction. Evaluation sources are
retained solver candidates; all roots, children, failures and repeats are logged.
This runner never changes the campaign or activates a solver default.
"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import zlib

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/narrow-update-20261002')
REPO = Path('/home/grant/decomp/sbk1')
digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())


def freeze():
    assert sys.platform == 'linux' and not str(WORK).startswith('/mnt/')
    WORK.mkdir(parents=True, exist_ok=False)
    code = WORK / 'code'
    for folder in ('solver', 'kb', 'miner', 'patterns', 'eval'):
        shutil.copytree(PROJECT / folder, code / folder,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', '*.sqlite', '*.pyc'))
    sys.path.insert(0, str(code))
    from solver import narrow_update
    protocol = read(HERE / 'protocol.json')
    excluded = set(protocol['excluded_previous_panel'])
    # Protected evaluation names are read as metadata only; do not open their C.
    for path in (PROJECT / 'eval/sets').glob('*.json'):
        excluded.update(row['function'] for row in read(path).get('heldout', []) if isinstance(row, dict) and 'function' in row)
    retained = {row['function']: row['source'] for row in
                map(json.loads, Path(protocol['root_population']).read_text().splitlines()) if row.get('source')}
    native = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
    pointer = read(native / 'campaign.json')
    with sqlite3.connect((native / pointer['store']).as_uri() + '?mode=ro', uri=True) as store:
        raw = store.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0]
        assert hashlib.sha256(raw).hexdigest() == pointer['sha256']
        manifest = json.loads(raw)
        with sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True) as research:
            selected = []
            for name in sorted(set(retained) - excluded):
                if name not in manifest['nodes']:
                    continue
                raw_node = zlib.decompress(store.execute('SELECT payload FROM objects WHERE hash=?', (manifest['nodes'][name],)).fetchone()[0])
                assert hashlib.sha256(raw_node).hexdigest() == manifest['nodes'][name]
                status = json.loads(raw_node)['status']
                if status in ('object_exact', 'integrated'):
                    continue
                exact_count = research.execute('SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1', (name,)).fetchone()[0]
                if exact_count or not next(narrow_update.variants(retained[name], name), None):
                    continue
                selected.append({'function': name, 'scope': 'prospectively-sealed-exposed-development',
                                 'source': retained[name], 'campaign_status_at_freeze': status,
                                 'research_exact_attempts_at_freeze': 0})
                if len(selected) == 12:
                    break
    previous = read(HERE.parent / 'compiler-causal-20261002/inputs.json')
    roots = [{'function': row['function'], 'scope': 'exposed-motivating-control',
              'source': Path(row['source']).read_text()} for row in previous['inputs']
             if row['function'] in protocol['controls']] + selected
    for row in roots:
        path = WORK / 'inputs' / (row['function'] + '.c')
        path.parent.mkdir(exist_ok=True)
        path.write_text(row.pop('source'))
        row.update(source=str(path), source_sha256=digest(path),
                   assistance='game headers and unknown historical candidate ancestry', training_eligible=False)
    pins = {str(p.relative_to(code)): digest(p) for p in code.rglob('*') if p.is_file()}
    bundle = {'roots': roots, 'code': str(code), 'code_pins': pins,
              'campaign_checkpoint_at_freeze': pointer['commit'], 'protocol_sha256': digest(HERE / 'protocol.json')}
    (WORK / 'bundle.json').write_text(json.dumps(bundle, indent=2) + '\n')
    (HERE / 'inputs.json').write_text(json.dumps(bundle, indent=2) + '\n')
    print(json.dumps({'controls': 2, 'selected_development_functions': len(selected), 'bundle': str(WORK / 'bundle.json')}))


def run():
    bundle = read(WORK / 'bundle.json')
    assert digest(HERE / 'protocol.json') == bundle['protocol_sha256']
    for path, expected in bundle['code_pins'].items():
        assert digest(Path(bundle['code']) / path) == expected
    sys.path.insert(0, bundle['code'])
    from eval import campaign_workers
    from solver import regalloc_search as rs, workspace
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    conn.executescript((Path(bundle['code']) / 'kb/schema.sql').read_text())
    if conn.execute('SELECT count(*) FROM functions').fetchone()[0] == 0:
        conn.execute('ATTACH DATABASE ? AS origin', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
        for table in ('extraction', 'tus', 'functions'):
            conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
        conn.commit()
        conn.execute('DETACH DATABASE origin')
    results = []
    for root in bundle['roots']:
        name = root['function']
        assert digest(root['source']) == root['source_sha256']
        source = Path(root['source']).read_text()
        case = {'function': name, 'scope': root['scope'], 'arms': []}
        for enabled in (False, True):
            arm = 'narrow_update' if enabled else 'ordinary'
            folder = WORK / name / arm
            folder.mkdir(parents=True, exist_ok=False)
            repo = campaign_workers.isolate(REPO, folder / 'repo', name)
            (repo / 'tools').unlink()
            shutil.copytree(REPO / 'tools', repo / 'tools', symlinks=True)
            ws = workspace.bootstrap(repo, name)
            target = (ws / 'target_object_dump_normalized.s').read_text()
            ids, rows = {}, []
            def evaluate(code, label, parent_source=None):
                tag = f'narrow_{len(rows):04d}'
                parent = ids.get(parent_source)
                attempt = workspace.score(ws, repo, tag, code, conn=conn, func=name,
                    strategy=f'narrow-update:{arm}:{label}'[:120], run_id='narrow-update-20261002',
                    parent_attempt_id=parent, relation='candidate-construction',
                    extra={'training_eligible': False, 'assistance': root['assistance'],
                           'root_source_sha256': root['source_sha256'], 'arm': arm})
                conn.commit()
                ids[code] = attempt.receipt_id
                normalized = ws / (tag + '_object_dump_normalized.s')
                dump = normalized.read_text() if attempt.compiled and normalized.exists() else None
                row = {'attempt_id': attempt.receipt_id, 'parent_attempt_id': parent, 'label': label,
                       'compiled': attempt.compiled, 'frontend_passed': (attempt.frontend or {}).get('passed'),
                       'exact': workspace.repair_complete(attempt),
                       'source_sha256': hashlib.sha256(code.encode()).hexdigest()}
                rows.append(row)
                (folder / (tag + '.c')).write_text(code)
                with (folder / 'attempts.jsonl').open('a') as log:
                    log.write(json.dumps(row) + '\n')
                return rs.Compiled(attempt.compiled, row['exact'], dump, attempt.diff,
                    evidence={'compiled': attempt.compiled, 'frontend': attempt.frontend,
                              'source_attribution': attempt.source_attribution, 'compiler_recipe': attempt.compiler_recipe})
            started = time.monotonic()
            baseline = evaluate(source, 'baseline')
            outcome = rs.search(name, source, lambda c, l: evaluate(c, l), target,
                compile_with_parent=evaluate, baseline=baseline, budget=24, depth=4, beam=3,
                narrow_updates=enabled)
            discovery_evaluations = len(rows)
            assert discovery_evaluations <= 25
            if outcome.exact and not baseline.exact:
                repeat = evaluate(outcome.best_source, 'independent-repeat', outcome.best_source)
                assert repeat.exact
                (folder / 'winner.c').write_text(outcome.best_source)
            record = {**outcome.summary(), 'arm': arm, 'discovery_evaluations_including_baseline': discovery_evaluations,
                      'confirmation_evaluations': len(rows) - discovery_evaluations,
                      'baseline_already_exact': baseline.exact, 'seconds': time.monotonic() - started,
                      'training_eligible': False}
            (folder / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
            case['arms'].append(record)
        results.append(case)
        (HERE / 'native-results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(case), flush=True)
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['freeze', 'run'])
    action = parser.parse_args().action
    freeze() if action == 'freeze' else run()

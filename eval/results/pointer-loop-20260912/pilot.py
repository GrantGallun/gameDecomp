"""Private, fully logged compiler/panel pilot; never promote or edit canonical C.

Run in WSL from the project root. Inputs are one immutable selected source/target
directory and an explicit JSON list of {label, source} variants. Each run creates
a new native workspace and an empty database containing only the selected
function/TU identity plus this run's complete attempt history.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers, semantic_lane
from solver import modelrepair, refine, workspace


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str) + '\n')
    temporary.replace(path)


def selected_identity(database, metadata, source):
    """One short read-only selected-row query; no history/database copy."""
    query = '''SELECT f.addr,f.name,f.size,f.insn_count,f.is_leaf,f.tu_id,
        t.name AS tu_name,t.object_path,t.start_addr,t.end_addr,t.compiler,t.flags,
        a.func_addr AS selected_address,a.source_code,a.source_sha256
        FROM functions f JOIN tus t ON t.id=f.tu_id JOIN attempts a ON a.id=?
        WHERE f.name=? LIMIT 2'''
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute(query, (metadata['attempt_id'], metadata['name']))]
    if len(rows) != 1:
        raise ValueError('selected function/TU identity is missing or ambiguous')
    row = rows[0]
    if (row['addr'] != metadata['address'] or row['size'] != metadata['size']
            or row['selected_address'] != row['addr'] or row['source_code'] != source
            or row['source_sha256'] != metadata['source_sha256'] or sha(source) != row['source_sha256']):
        raise ValueError('selected source/address/size differs from retained metadata')
    return row


def seed_database(path, identity):
    conn = sqlite3.connect(path)
    refine.ensure_schema(conn)
    conn.execute('INSERT INTO tus(id,name,object_path,start_addr,end_addr,compiler,flags) VALUES (?,?,?,?,?,?,?)',
                 tuple(identity[k] for k in ('tu_id', 'tu_name', 'object_path', 'start_addr', 'end_addr', 'compiler', 'flags')))
    conn.execute('INSERT INTO functions(addr,name,tu_id,size,insn_count,is_leaf,state,attempts) VALUES (?,?,?,?,?,?,?,0)',
                 tuple(identity[k] for k in ('addr', 'name', 'tu_id', 'size', 'insn_count', 'is_leaf')) + ('asm',))
    conn.execute('CREATE TABLE pilot_semantic_receipts (attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id), '
                 'source_sha256 TEXT NOT NULL, report TEXT NOT NULL, created_at REAL NOT NULL)')
    conn.commit()
    return conn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--variants', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--database', type=Path, default=ROOT / 'eval/results/resume-pipeline-20260908/campaign.sqlite')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--cases', type=int, default=64)
    parser.add_argument('--steps', type=int, default=10000)
    args = parser.parse_args()
    if not 1 <= args.cases <= 256 or not 1 <= args.steps <= 100000:
        raise ValueError('semantic budgets outside the bounded pilot limits')
    selection = args.selection.resolve(strict=True)
    metadata = json.loads((selection / 'metadata.json').read_text())
    name = metadata['name']
    if not re.fullmatch(r'[A-Za-z_]\w*', name):
        raise ValueError('invalid selected function name')
    selected_source = selection / 'source.c'
    if not selected_source.exists():
        selected_source = selection / 'selected.c'
    source = selected_source.read_text()
    target = (selection / 'target.s').read_bytes()
    if sha(source) != metadata['source_sha256'] or sha(target) != metadata['target_sha256']:
        raise ValueError('selected source or target artifact changed')
    variants = json.loads(args.variants.read_text())
    if not isinstance(variants, list) or len(variants) > 32:
        raise ValueError('variants must be an explicit list of at most32 candidates')
    if any(not isinstance(row, dict) or not isinstance(row.get('label'), str)
           or not row['label'] or not isinstance(row.get('source'), str) or not row['source'].strip()
           for row in variants):
        raise ValueError('every variant requires nonempty label/source strings')
    if any(row.get('parent_source_sha256', sha(source)) != sha(source) for row in variants):
        raise ValueError('variant parent source hash differs from selected source')
    output = (args.out or Path('/home/grant/decomp/pointer-loop-20260912') /
              (name + '-' + str(time.time_ns()))).absolute()
    if str(output).startswith('/mnt/'):
        raise ValueError('compiler workspaces must be on the native WSL filesystem')
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    run_id = 'pointer-loop-' + output.name
    (output / 'inputs').mkdir()
    shutil.copyfile(selection / 'metadata.json', output / 'inputs/metadata.json')
    (output / 'inputs/source.c').write_text(source)
    (output / 'inputs/target.s').write_bytes(target)
    write(output / 'inputs/variants.json', variants)
    report = {'schema_version': 1, 'kind': 'private-pointer-loop-compiler-pilot',
              'function': name, 'output': str(output), 'selection': str(selection),
              'selected_attempt_id': metadata['attempt_id'], 'selected_source_sha256': sha(source),
              'selected_target_sha256': sha(target), 'model_calls': 0,
              'canonical_edits': False, 'promotion': False, 'live_database_copy': False,
              'run_id': run_id, 'started_at': time.time(), 'attempts': []}
    write(output / 'summary.json', report)
    conn = None
    states = []
    try:
        identity = selected_identity(args.database.resolve(strict=True), metadata, source)
        write(output / 'inputs/selected-identity.json', identity)
        conn = seed_database(output / 'history.sqlite', identity)
        repo = campaign_workers.isolate(args.repo.resolve(strict=True), output / 'repo', name)
        ws = repo / 'nonmatchings' / name
        if sha((ws / 'target.s').read_bytes()) != metadata['target_sha256']:
            raise ValueError('isolated target differs from retained selected target')
        report.update(private_database=str(output / 'history.sqlite'), workspace=str(ws),
                      compiler_tu=identity['tu_name'])
        panel = semantic_lane.DeferredPanel(repo, ws, name, args.cases, args.steps)

        def evaluate_semantic(index):
            state = states[index]
            row = report['attempts'][index]
            started = time.monotonic()
            try:
                semantic = panel(state)
                if semantic is None:
                    semantic = {'status': 'not_evaluated', 'reason': 'compile/frontend gate did not pass',
                                'authoritative': False}
            except Exception as error:
                semantic = {'status': 'unavailable', 'reason': f'{type(error).__name__}: {error}',
                            'traceback': traceback.format_exc(), 'authoritative': False}
            row['semantic'] = semantic
            row['semantic_seconds'] = time.monotonic() - started
            row['observed_semantic_pass'] = semantic.get('status') == 'observed_pass'
            row['gates']['semantic'] = semantic.get('status')
            row['gates']['object_exact_frontend_and_observed_pass'] = bool(
                state.attempt.exact and row['gates']['frontend_passed'] and row['observed_semantic_pass'])
            write(output / f'semantic-{index:03d}.json', semantic)
            conn.execute('INSERT INTO pilot_semantic_receipts VALUES (?,?,?,?)',
                         (state.attempt.receipt_id, sha(state.source), json.dumps(semantic), time.time()))
            conn.commit()
            write(output / 'panel-report.json', panel.report)
            write(output / 'summary.json', report)

        for index, item in enumerate([{'label': 'baseline', 'source': source}] + variants):
            tag = f'pointer_loop_{index:03d}'
            parent = states[0].attempt.receipt_id if states else None
            logging = dict(run_id=run_id, run_kind='private-pointer-loop-pilot',
                           run_config={'selection': str(selection), 'selected_source_sha256': sha(source),
                                       'model_calls': 0, 'canonical_import': False},
                           iteration=index, parent_attempt_id=parent,
                           relation='pointer-loop-source-variant' if index else 'selected-source-replay',
                           strategy=item['label'], action=item['label'],
                           extra={'external_selected_attempt_id': metadata['attempt_id'],
                                  'external_selected_database': str(args.database),
                                  'source_bound_parent_sha256': sha(source)})
            started = time.monotonic()
            try:
                attempt = workspace.score(ws, repo, tag, item['source'], conn=conn, func=name, **logging)
            except Exception as error:
                details = f'{type(error).__name__}: {error}\n' + traceback.format_exc()
                attempt = workspace.Attempt(False, 0.0, False, '', str(error), details)
                workspace.record_attempt(conn, name, item['source'], attempt, **logging)
            if attempt.receipt_id is None:
                raise RuntimeError('compiler attempt was not durably logged')
            elapsed = time.monotonic() - started
            conn.execute('UPDATE attempts SET wall_ms=? WHERE id=?', (round(elapsed * 1000), attempt.receipt_id))
            conn.commit()
            state = modelrepair.CandidateState(item['source'], attempt, ws / (tag + '.o'))
            states.append(state)
            artifacts = {}
            for path in sorted(ws.glob(tag + '*')):
                if path.is_file():
                    artifacts[path.name] = {'path': str(path), 'sha256': sha(path.read_bytes())}
            frontend = (attempt.frontend or {}).get('passed') is True
            boundary = (attempt.verification or {}).get('function_boundary', {}).get('function_exact') is True
            row = {'index': index, 'label': item['label'], 'source_sha256': sha(item['source']),
                   'attempt_id': attempt.receipt_id, 'parent_attempt_id': parent,
                   'compiled': attempt.compiled, 'score': attempt.score, 'exact': attempt.exact,
                   'frontend': attempt.frontend, 'verification': attempt.verification,
                   'compiler_seconds': elapsed, 'artifacts': artifacts,
                   'gates': {'frontend_passed': frontend, 'object_exact': attempt.exact,
                             'function_boundary_exact': boundary,
                             'repair_complete': workspace.repair_complete(attempt),
                             'semantic': 'not_selected_yet'},
                   'semantic': None}
            (output / f'attempt-{index:03d}.source.c').write_text(item['source'])
            (output / f'attempt-{index:03d}.diff.txt').write_text(attempt.diff)
            (output / f'attempt-{index:03d}.compiler.txt').write_text(attempt.raw_output)
            write(output / f'attempt-{index:03d}.json', {**row, 'full_attempt': asdict(attempt)})
            report['attempts'].append(row)
            write(output / 'summary.json', report)
            print(json.dumps({key: row[key] for key in ('index', 'label', 'attempt_id', 'compiled', 'score', 'exact')}), flush=True)
        baseline_score = states[0].attempt.score
        eligible = [i for i in range(1, len(states)) if states[i].attempt.compiled]
        ranked = sorted(eligible, key=lambda i: (-states[i].attempt.score, i))
        selected = set(ranked[:3]) | {i for i in eligible if states[i].attempt.score > baseline_score
                    or states[i].attempt.exact or report['attempts'][i]['gates']['function_boundary_exact']}
        evaluate_semantic(0)
        for i in sorted(selected):
            evaluate_semantic(i)
        for i, row in enumerate(report['attempts']):
            row['delta_from_private_baseline'] = row['score'] - baseline_score
            if i and i not in selected:
                row['gates']['semantic'] = 'not_selected_compile_failed_or_outside_top3_and_not_improved'
        report['totals'] = {'compile_attempts': len(states), 'compiled': sum(s.attempt.compiled for s in states),
                            'semantic_evaluations': 1 + len(selected),
                            'improved': sum(states[i].attempt.score > baseline_score for i in eligible),
                            'object_exact': sum(s.attempt.exact for s in states),
                            'frontend_passed': sum(r['gates']['frontend_passed'] for r in report['attempts'])}
        report['status'] = 'complete'
    except Exception as error:
        report.update(status='error', error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
        raise
    finally:
        if conn is not None:
            report['private_history_counts'] = {table: conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                                               for table in ('functions', 'tus', 'attempts', 'attempt_edges', 'pilot_semantic_receipts')}
            conn.close()
        report['finished_at'] = time.time()
        write(output / 'summary.json', report)
        print(json.dumps({'output': str(output), 'status': report.get('status'), 'totals': report.get('totals')}), flush=True)


if __name__ == '__main__':
    main()

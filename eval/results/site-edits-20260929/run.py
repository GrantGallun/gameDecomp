"""Run solver.site_edits on the frame; see README.md for the pre-registration.

    cd /mnt/c/Code/gameDecomp && ~/decomp/sbk1/.venv/bin/python eval/results/site-edits-20260929/run.py \
        --frame eval/results/site-edits-20260929/frame.json --workers 4
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
REPO = Path('/home/grant/decomp/sbk1')
CAMPAIGN = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite')
TRIAL = Path('/home/grant/decomp/runs/site-edits-20260929/trial.sqlite')


def init_db() -> None:
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if TRIAL.exists():
        return
    conn = sqlite3.connect(TRIAL)
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS c', (CAMPAIGN.resolve().as_uri() + '?mode=ro',))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM c.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE c')
    conn.close()


def _register_finish(ws, conn, function: str, source: str, run_id: str, budget: int = 80) -> dict:
    """Hand a register-only residual to the existing register search (unkeyed, same oracle)."""
    from solver import regalloc_search, workspace

    def compile_with_parent(candidate, label, parent_source):
        tag = f'{function}_siteedit_rs_{time.time_ns()}'
        attempt = workspace.score(ws, REPO, tag, candidate, conn=conn, func=function,
                                  strategy=f'site-edits-regalloc:{label}'[:120], run_id=run_id,
                                  run_kind='site-edits')
        conn.commit()
        dump = ws / f'{tag}_object_dump_normalized.s'
        text = dump.read_text() if attempt.compiled and dump.is_file() else None
        evidence = {'compiled': bool(attempt.compiled), 'score': attempt.score,
                    'source_attribution': attempt.source_attribution,
                    'frontend': attempt.frontend, 'compiler_recipe': attempt.compiler_recipe}
        return regalloc_search.Compiled(bool(attempt.compiled), workspace.repair_complete(attempt),
                                        text, attempt.diff or '', evidence)

    target = (ws / 'target_object_dump_normalized.s').read_text()
    outcome = regalloc_search.search(function, source, compile_with_parent, target, budget=budget,
                                     enable=True, compile_with_parent=compile_with_parent, coalesce=True)
    return {'exact': bool(outcome.exact), **outcome.summary()}


def one(item: dict) -> dict:
    from solver import site_edits, workspace
    function = item['name']
    campaign = sqlite3.connect(CAMPAIGN.resolve().as_uri() + '?mode=ro', uri=True)
    source = campaign.execute('SELECT source_code FROM attempts WHERE id=?', (item['attempt_id'],)).fetchone()[0]
    campaign.close()
    conn = sqlite3.connect(TRIAL, timeout=600)
    ws = workspace.bootstrap(REPO, function)
    run_id = f'site-edits-{int(time.time())}-{function}'

    def score(code: str, label: str, parent=None):
        attempt = workspace.score(ws, REPO, f'{function}_siteedit_{time.time_ns()}', code, conn=conn,
                                  func=function, strategy=f'site-edits:{label}'[:120], run_id=run_id,
                                  run_kind='site-edits')
        conn.commit()
        return attempt

    started = time.time()
    try:
        result = site_edits.search(score, source, function)
        if not result['exact'] and result.get('register_only'):
            result['handoff'] = _register_finish(ws, conn, function, result['source'], run_id)
    except Exception as exc:          # a harness failure is a finding, recorded, not swallowed
        result = {'exact': False, 'error': repr(exc)[:500]}
    conn.close()
    return {'function': function, 'size': item['size'], 'residual_lines': item['total'],
            'seconds': round(time.time() - started, 1), **result}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame', type=Path, required=True)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--out', type=Path, default=HERE / 'results.jsonl')
    args = ap.parse_args()
    init_db()
    frame = json.loads(args.frame.read_text())
    if args.only:
        frame = [f for f in frame if f['name'] in set(args.only)]
    with multiprocessing.Pool(args.workers) as pool, args.out.open('a') as out:
        for row in pool.imap_unordered(one, frame):
            out.write(json.dumps(row) + '\n')
            out.flush()
            print(f"{row['function']:48s} exact={row['exact']} {row.get('baseline')} -> {row.get('best')} "
                  f"compiles={row.get('compiles')} {row.get('error', '')}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

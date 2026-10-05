"""Run solver.site_edits on the width frame; see PREREGISTRATION.md.

    cd /mnt/c/Code/gameDecomp && ~/decomp/sbk1/.venv/bin/python eval/results/width-edits-20260929/run.py --workers 8

Attempts are logged to a trial database, not the campaign ledger. No reference source, no model.
"""
from __future__ import annotations

import argparse
import difflib
import json
import multiprocessing
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
REPO = Path('/home/grant/decomp/sbk1')
LEDGERS = {'campaign': Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite'),
           'kb': Path('/home/grant/decomp/kb-sbk1.sqlite')}
TRIAL = Path('/home/grant/decomp/runs/composed-edits-20260929/ucopy3.sqlite')
EXT = re.compile(r"^(?:(?:sll|sra|srl)\s+\w+,\w+,(?:0x10|16|0x18|24)|andi\s+\w+,\w+,(?:0xff|0xffff))$")
WIDTH_KINDS = ('decl', 'type')


def extension_units(diff: str) -> int:
    """Extension instructions present on one side only (same count the frame was selected on)."""
    from solver import diffrepair
    t, c = diffrepair._streams(diff or '')
    n = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, t, c, autojunk=False).get_opcodes():
        if tag != 'equal':
            n += sum(1 for x in t[i1:i2] if EXT.match(x)) + sum(1 for x in c[j1:j2] if EXT.match(x))
    return n


def init_db() -> None:
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if TRIAL.exists():
        return
    conn = sqlite3.connect(TRIAL)
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS c', (LEDGERS['campaign'].resolve().as_uri() + '?mode=ro',))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM c.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE c')
    conn.close()


def one(item: dict) -> dict:
    from solver import site_edits, workspace
    function = item['name']
    ledger = sqlite3.connect(LEDGERS[item['ledger']].resolve().as_uri() + '?mode=ro', uri=True)
    source = ledger.execute('SELECT source_code FROM attempts WHERE id=?', (item['attempt_id'],)).fetchone()[0]
    ledger.close()
    conn = sqlite3.connect(TRIAL, timeout=600)
    run_id = f'composed-ucopy3-{int(time.time())}-{function}'
    units = {}
    started = time.time()
    try:
        ws = workspace.bootstrap(REPO, function)

        def score(code: str, label: str, parent=None):
            attempt = workspace.score(ws, REPO, f'{function}_ucopy_{time.time_ns()}', code, conn=conn,
                                      func=function, strategy=f'composed-ucopy3:{label}'[:120], run_id=run_id,
                                      run_kind='composed-ucopy3')
            conn.commit()
            if attempt.compiled:
                units[label] = min(units.get(label, 10**9), extension_units(attempt.diff))
            return attempt

        from solver import residual_classes
        result = site_edits.search(score, source, function, key=residual_classes.key, focus=residual_classes.focus)
    except Exception as exc:          # a harness failure is a finding, recorded, not swallowed
        result = {'exact': False, 'error': repr(exc)[:500], 'trail': []}
    conn.close()
    trail = result.pop('trail', [])
    shape_tried = [t for t in trail if str(t.get('kind', '')).startswith('shape:')]
    proposals = [t for t in trail if 'proposals' in t or 'declined' in t]
    tried = [t for t in trail if 'kind' in t]
    width_tried = [t for t in tried if t['kind'] in WIDTH_KINDS]
    base_units = units.get('baseline')
    child_units = [v for k, v in units.items() if k != 'baseline']
    return {'function': function, 'band': item['band'], 'ledger': item['ledger'],
            'seconds': round(time.time() - started, 1),
            'exact': result.get('exact'), 'baseline': result.get('baseline'), 'best': result.get('best'),
            'compiles': result.get('compiles'), 'error': result.get('error'),
            'baseline_gradient': result.get('baseline_gradient'), 'best_gradient': result.get('best_gradient'),
            'level0_receipt': proposals[0] if proposals else None,
            'width_edits_tried': len(width_tried), 'edits_tried': len(tried), 'shape_edits_tried': len(shape_tried),
            'kinds_on_best_path': [t['kind'] for t in tried if t.get('complete')],
            'width_edit_kinds_won': sorted({t['edit'] for t in width_tried if t.get('complete')}),
            'extension_units_baseline': base_units,
            'extension_units_min_child': min(child_units) if child_units else None,
            'source_best': result.get('source') if result.get('exact') else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--frame', type=Path, default=HERE / 'frame.json')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--only', nargs='*', default=None)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--out', type=Path, default=HERE / 'results.jsonl')
    args = ap.parse_args()
    init_db()
    frame = json.loads(args.frame.read_text())
    done = set()
    if args.out.exists():
        done = {json.loads(l)['function'] for l in args.out.read_text().splitlines() if l.strip()}
    frame = [f for f in frame if f['name'] not in done and (not args.only or f['name'] in set(args.only))]
    if args.limit:
        frame = frame[:args.limit]
    with multiprocessing.Pool(args.workers) as pool, args.out.open('a') as out:
        for row in pool.imap_unordered(one, frame):
            out.write(json.dumps(row) + '\n')
            out.flush()
            print(f"{row['function']:48s} exact={row['exact']} {row.get('baseline')} -> {row.get('best')} "
                  f"compiles={row.get('compiles')} ext={row['extension_units_baseline']}->"
                  f"{row['extension_units_min_child']} width_tried={row['width_edits_tried']} "
                  f"{row.get('error') or ''}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

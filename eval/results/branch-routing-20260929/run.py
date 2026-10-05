"""Compile every never-compiled branch_shape variant on the frame's best states; see PREREGISTRATION.md."""
from __future__ import annotations
import json, multiprocessing, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
REPO = Path('/home/grant/decomp/sbk1')
LEDGERS = [Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite'), Path('/home/grant/decomp/kb-sbk1.sqlite')]
TRIAL = Path('/home/grant/decomp/runs/branch-routing-20260929/trial.sqlite')


def init_db():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if TRIAL.exists():
        return
    conn = sqlite3.connect(TRIAL)
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS c', (LEDGERS[0].resolve().as_uri() + '?mode=ro',))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM c.{table}')
    conn.commit(); conn.execute('DETACH DATABASE c'); conn.close()


def best_source(function):
    best = None
    for p in LEDGERS:
        db = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True)
        r = db.execute('select a.score, a.source_code from attempts a join functions f on f.addr=a.func_addr '
                       'where f.name=? and a.compiled=1 and a.exact=0 order by a.score desc limit 1', (function,)).fetchone()
        db.close()
        if r and (best is None or r[0] > best[0]):
            best = r
    return best[1]


def one(function):
    from solver import branch_shape, site_edits, workspace
    conn = sqlite3.connect(TRIAL, timeout=600)
    run_id = f'branch-routing-{int(time.time())}-{function}'
    started = time.time()
    try:
        ws = workspace.bootstrap(REPO, function)
        source = best_source(function)

        def score(code, label):
            a = workspace.score(ws, REPO, f'{function}_brroute_{time.time_ns()}', code, conn=conn, func=function,
                                strategy=f'branch-routing:{label}'[:120], run_id=run_id, run_kind='branch-routing')
            conn.commit()
            return a
        base = score(source, 'baseline')
        rows = []
        for label, kind, cand in branch_shape.variants(source, function, base.diff or ''):
            a = score(cand, f'{kind}:{label}')
            rows.append({'kind': kind, 'label': label, 'compiled': bool(a.compiled), 'score': a.score,
                         'exact': workspace.repair_complete(a),
                         'gradient': list(site_edits.gradient(a)) if a.compiled else None,
                         'stderr': None if a.compiled else (a.compiler_stderr or '')[-300:]})
        out = {'function': function, 'baseline': base.score, 'baseline_gradient': list(site_edits.gradient(base)),
               'variants': rows}
    except Exception as exc:
        out = {'function': function, 'error': repr(exc)[:500], 'variants': []}
    out['seconds'] = round(time.time() - started, 1)
    conn.close()
    return out


def main():
    init_db()
    frame = [n for n, k in json.loads((ROOT / 'eval/results/structural-residual-20260929/branch_fire_new.json').read_text()).items() if k]
    out = HERE / 'results.jsonl'
    done = {json.loads(l)['function'] for l in out.read_text().splitlines()} if out.exists() else set()
    frame = [f for f in frame if f not in done]
    with multiprocessing.Pool(8) as pool, out.open('a') as fh:
        for row in pool.imap_unordered(one, frame):
            fh.write(json.dumps(row) + '\n'); fh.flush()
            v = row['variants']
            print(f"{row['function']:48s} base={row.get('baseline')} n={len(v)} best={max([x['score'] or 0 for x in v] or [None])} "
                  f"exact={any(x['exact'] for x in v)} broke={sum(not x['compiled'] for x in v)} {row.get('error') or ''}", flush=True)


if __name__ == '__main__':
    main()

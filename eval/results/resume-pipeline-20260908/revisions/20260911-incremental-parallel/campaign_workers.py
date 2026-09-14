"""Private worker databases, append-only lineage import, and stable workspaces."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

TABLES = ('attempt_runs', 'attempts', 'attempt_edges', 'model_proposals')
ATTEMPT_KEYS = {'attempt_id', 'best_attempt_id', 'parent_attempt_id', 'child_attempt_id',
                'source_parent_attempt_id', 'receipt_id', 'seed_attempt_id'}


def remap(value, attempts, proposals):
    if isinstance(value, list):
        return [remap(v, attempts, proposals) for v in value]
    if isinstance(value, dict):
        return {k: (attempts.get(v, v) if k in ATTEMPT_KEYS and isinstance(v, int) else
                    proposals.get(v, v) if k == 'proposal_id' and isinstance(v, int) else
                    remap(v, attempts, proposals)) for k,v in value.items()}
    return value


def maxima(conn):
    return {t: conn.execute(f'SELECT COALESCE(MAX(id),0) FROM {t}').fetchone()[0]
            for t in ('attempts', 'model_proposals')}


def synchronize(main, private, prior=None):
    """First full consistent backup; subsequently replace only new trajectory rows.

    Existing binary, inference, and historical rows are not filtered out. All
    workers see the complete committed DB history at each dispatch boundary.
    """
    private = Path(private)
    if not private.exists():
        temporary = private.with_suffix('.initial.sqlite')
        temporary.unlink(missing_ok=True)
        with sqlite3.connect(f'file:{Path(main).as_posix()}?mode=ro', uri=True) as src:
            with sqlite3.connect(temporary) as dst:
                src.backup(dst)
                result = maxima(dst)
        temporary.replace(private)
        return result
    if prior is None:
        # No job can be dispatched until its marker and inflight commit exist.
        # This is recovery from a completed initial copy before marker creation.
        with sqlite3.connect(private) as conn:
            prior = maxima(conn)
    with sqlite3.connect(private, timeout=120, uri=True) as dst:
        dst.execute('ATTACH DATABASE ? AS upstream', (f'file:{Path(main).as_posix()}?mode=ro',))
        # Only local append-only work is discarded here, after durable import.
        dst.execute('DELETE FROM attempt_edges WHERE child_attempt_id>?', (prior['attempts'],))
        dst.execute('DELETE FROM model_proposals WHERE id>?', (prior['model_proposals'],))
        dst.execute('DELETE FROM attempts WHERE id>?', (prior['attempts'],))
        dst.execute('DELETE FROM attempt_runs WHERE id NOT IN (SELECT id FROM upstream.attempt_runs)')
        dst.execute('INSERT OR IGNORE INTO attempt_runs SELECT * FROM upstream.attempt_runs')
        dst.execute('INSERT INTO attempts SELECT * FROM upstream.attempts WHERE id>?', (prior['attempts'],))
        dst.execute('INSERT INTO model_proposals SELECT * FROM upstream.model_proposals WHERE id>?', (prior['model_proposals'],))
        dst.execute('INSERT INTO attempt_edges SELECT * FROM upstream.attempt_edges WHERE child_attempt_id>?', (prior['attempts'],))
        return maxima(dst)


def merge(main, private, cutoffs, job_id):
    """Transactionally import rows and retain an idempotency receipt in the same DB."""
    with sqlite3.connect(main, timeout=120) as dst, sqlite3.connect(private) as src:
        dst.execute('PRAGMA foreign_keys=ON')
        dst.execute('CREATE TABLE IF NOT EXISTS campaign_worker_imports(job_id TEXT PRIMARY KEY,mapping TEXT NOT NULL)')
        dst.commit()
        dst.execute('BEGIN IMMEDIATE')
        previous = dst.execute('SELECT mapping FROM campaign_worker_imports WHERE job_id=?', (job_id,)).fetchone()
        if previous:
            data = json.loads(previous[0])
            return ({int(k):v for k,v in data['attempts'].items()},
                    {int(k):v for k,v in data['proposals'].items()})
        src.row_factory = sqlite3.Row
        attempts = [dict(r) for r in src.execute('SELECT * FROM attempts WHERE id>? ORDER BY id', (cutoffs['attempts'],))]
        proposals = [dict(r) for r in src.execute('SELECT * FROM model_proposals WHERE id>? ORDER BY id', (cutoffs['model_proposals'],))]
        limits = maxima(dst)
        amap = {r['id']:limits['attempts']+i+1 for i,r in enumerate(attempts)}
        pmap = {r['id']:limits['model_proposals']+i+1 for i,r in enumerate(proposals)}
        runs = {r['run_id'] for r in attempts+proposals if r.get('run_id')}
        def insert(table, row):
            cols = list(row)
            dst.execute(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({",".join("?" for _ in cols)})',
                        tuple(row[c] for c in cols))
        for run in runs:
            row = dict(src.execute('SELECT * FROM attempt_runs WHERE id=?', (run,)).fetchone())
            old = dst.execute('SELECT * FROM attempt_runs WHERE id=?', (run,)).fetchone()
            if old is None:
                insert('attempt_runs', row)
            elif tuple(row.values()) != tuple(old):
                raise ValueError('worker run identity collision')
        for table, rows, mapping in [('attempts', attempts, amap), ('model_proposals', proposals, pmap)]:
            for row in rows:
                row = remap(row, amap, pmap)
                row['id'] = mapping[row['id']]
                for column in ('sampling',):
                    if row.get(column):
                        row[column] = json.dumps(remap(json.loads(row[column]), amap, pmap), sort_keys=True)
                insert(table, row)
        for row in src.execute('SELECT * FROM attempt_edges WHERE child_attempt_id>?', (cutoffs['attempts'],)):
            insert('attempt_edges', remap(dict(row), amap, pmap))
        dst.execute('INSERT INTO campaign_worker_imports VALUES (?,?)',
                    (job_id, json.dumps({'attempts':amap, 'proposals':pmap})))
        return amap, pmap


def isolate(repo, directory, function):
    repo, directory = Path(repo), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile', 'symbol_addrs.txt',
                 'snowboardkids.yaml', 'snowboardkids.z64', 'build', 'undefined_syms_auto.txt', 'undefined_syms.txt'):
        path = repo/name
        if path.exists() and not (directory/name).exists():
            (directory/name).symlink_to(path, target_is_directory=path.is_dir())
    ws = directory/'nonmatchings'/function
    ws.mkdir(parents=True, exist_ok=True)
    for path in (repo/'nonmatchings'/function).iterdir():
        if path.is_file() and (path.suffix == '.py' or path.name.startswith('target') or
                path.name in {'build.sh','base.c','prelude.inc','.diff_algorithm','.compiler-target.json'}):
            dest = ws/path.name
            if not dest.exists() or dest.read_bytes() != path.read_bytes():
                shutil.copy2(path, dest)
    return directory

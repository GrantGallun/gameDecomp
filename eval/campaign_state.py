"""Incremental, content-addressed campaign snapshots with transactional history.

The small JSON file is the commit pointer. SQLite objects/commits are immutable;
an interrupted pointer replacement leaves the previous complete snapshot valid.
Legacy checkpoints remain readable and exportable without losing any fields.
"""
import hashlib
import copy
import json
import os
from pathlib import Path
import sqlite3
import time
import zlib
from collections import Counter
from contextlib import closing

KIND = 'campaign-checkpoint-index-v1'


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode()


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read(path):
    path = Path(path)
    pointer = json.loads(path.read_bytes())
    if pointer.get('kind') != KIND:
        return pointer
    db = path.parent / pointer['store']
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        return _hydrate(conn, pointer)


def _hydrate(conn, pointer):
    row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
    if row is None or not isinstance(row[0], bytes) or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
        raise ValueError('campaign commit missing or corrupt')
    manifest = json.loads(row[0])
    hashes = {manifest['metadata'], *manifest['nodes'].values()}
    objects = {}
    for key, compressed in conn.execute('SELECT hash,payload FROM objects WHERE hash IN (SELECT value FROM json_each(?))',
                                        (json.dumps(sorted(hashes)),)):
        raw = zlib.decompress(compressed)
        if hashlib.sha256(raw).hexdigest() != key:
            raise ValueError('campaign object checksum mismatch')
        objects[key] = json.loads(raw)
    if objects.keys() != hashes:
        raise ValueError('campaign object missing')
    state = objects[manifest['metadata']]
    seen = set()
    nodes = {}
    for name,key in manifest['nodes'].items():
        nodes[name] = copy.deepcopy(objects[key]) if key in seen else objects[key]
        seen.add(key)
    state['nodes'] = nodes
    return state


def _sqlite_diagnostic(exc, db, operation):
    return {'operation': operation, 'database': str(db),
            'sqlite_errorcode': getattr(exc, 'sqlite_errorcode', None),
            'sqlite_errorname': getattr(exc, 'sqlite_errorname', None), 'error': str(exc)}


def read_for_resume(path):
    """Controller only: caller MUST hold the campaign lock through all work.

    A read-only SQLite connection cannot roll back an interrupted transaction.
    Recover only that explicit extended error, never corrupt data, missing files,
    generic I/O errors, a different commit, or a busy writer. Ordinary readers
    keep using read(), which never opens a writable connection.
    """
    path = Path(path)
    original = path.read_bytes()
    pointer = json.loads(original)
    if pointer.get('kind') != KIND:
        return pointer
    expected = path.with_suffix('.state.sqlite')
    if pointer.get('store') != expected.name or expected.is_symlink():
        raise ValueError('unexpected campaign object store for controller recovery')
    db = expected.resolve()
    try:
        return read(path)
    except sqlite3.OperationalError as exc:
        journal = Path(str(db) + '-journal')
        if (getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_READONLY_ROLLBACK
                or not journal.is_file() or journal.stat().st_size == 0):
            raise
        diagnostic = _sqlite_diagnostic(exc, db, 'resume hot-journal recovery')
    if path.read_bytes() != original:
        raise ValueError('checkpoint pointer changed before recovery')
    # mode=rw cannot create a missing store. SQLite owns journal rollback; never
    # remove journal files or retry a failed save ourselves.
    with closing(sqlite3.connect(db.as_uri() + '?mode=rw', uri=True, timeout=120)) as conn:
        if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('checkpoint integrity check failed after journal recovery')
        state = _hydrate(conn, pointer)
    if path.read_bytes() != original:
        raise ValueError('checkpoint pointer changed during recovery')
    receipt = {**diagnostic, 'status': 'recovered', 'time': time.time(),
               'checkpoint': pointer['commit'], 'manifest_sha256': pointer['sha256'],
               'pointer_sha256': hashlib.sha256(original).hexdigest(),
               'selected_objects_verified': True, 'integrity_check': 'ok'}
    receipts = path.parent / (path.stem + '-recovery')
    receipts.mkdir(exist_ok=True)
    atomic(receipts / (str(time.time_ns()) + '.json'), receipt)
    return state


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.db = self.path.with_suffix('.state.sqlite')
        self.refs = {}
        self.commit = None
        if self.path.exists():
            pointer = json.loads(self.path.read_bytes())
            if pointer.get('kind') == KIND:
                if pointer['store'] != self.db.name:
                    raise ValueError('unexpected campaign object store')
                with closing(sqlite3.connect(self.db)) as conn:
                    row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
                    if not row or not isinstance(row[0], bytes) or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
                        raise ValueError('campaign commit missing or corrupt')
                    self.refs = json.loads(row[0])['nodes']
                    self.commit = pointer['commit']

    def save(self, state, changed=()):
        try:
            return self._save(state, changed)
        except sqlite3.Error as exc:
            exc.add_note(json.dumps(_sqlite_diagnostic(exc, self.db, 'checkpoint save'), sort_keys=True))
            raise

    def _save(self, state, changed=()):
        started = time.monotonic()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        refs = {name: key for name, key in self.refs.items() if name in state['nodes']}
        dirty = set(changed) | (state['nodes'].keys() - refs.keys())
        with closing(sqlite3.connect(self.db, timeout=120)) as conn, conn:
            conn.execute('PRAGMA synchronous=FULL')
            conn.execute('CREATE TABLE IF NOT EXISTS objects(hash TEXT PRIMARY KEY,payload BLOB NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS commits(id INTEGER PRIMARY KEY,parent INTEGER,manifest BLOB NOT NULL,created REAL NOT NULL)')
            def put(value):
                raw = encode(value)
                key = hashlib.sha256(raw).hexdigest()
                if not conn.execute('SELECT 1 FROM objects WHERE hash=?', (key,)).fetchone():
                    conn.execute('INSERT INTO objects VALUES (?,?)', (key, zlib.compress(raw, 1)))
                return key
            for name in sorted(dirty):
                refs[name] = put(state['nodes'][name])
            metadata = put({k:v for k,v in state.items() if k != 'nodes'})
            manifest = encode({'nodes': refs, 'metadata': metadata})
            commit = conn.execute('INSERT INTO commits(parent,manifest,created) VALUES (?,?,?)',
                                  (self.commit, manifest, time.time())).lastrowid
        pointer = {'kind': KIND, 'store': self.db.name, 'commit': commit,
                   'sha256': hashlib.sha256(manifest).hexdigest(), 'updated_at': time.time(),
                   'status': state.get('status'), 'summary': state.get('summary'),
                   'fast_metrics': state.get('fast_metrics'),
                   'reader': 'python -m eval.campaign_state CHECKPOINT --export FULL_JSON'}
        pointer['health'] = {'functions':len(state['nodes']),
            'states':dict(Counter(n['status'] for n in state['nodes'].values())),
            'semantic_functions':dict(Counter(n['semantic_validation'].get('status')
                for n in state['nodes'].values() if n.get('semantic_validation'))),
            'inflight':state.get('inflight'),
            'parallel_inflight':[{k:j.get(k) for k in ('id','function','profile')}
                                 for j in state.get('fast_inflight',[])],
            'performance':state.get('fast_metrics')}
        atomic(self.path, pointer)
        self.refs, self.commit = refs, commit
        return time.monotonic() - started


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('--export', required=True, type=Path)
    args = parser.parse_args()
    if args.export.exists():
        raise SystemExit('export must be a new file')
    atomic(args.export, read(args.checkpoint))

"""Small, incremental read-only projection of immutable campaign snapshots."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import threading
import time
import zlib
from urllib.parse import quote


def project(name, node, group):
    residual = node.get('residual') or {}
    status = node.get('status', 'unknown')
    compiled = residual.get('compiled')
    jobs = node.get('jobs') or []
    category = ('exact' if status in {'object_exact', 'integrated'} else
                'function_exact' if status == 'function_exact_pending_integration' else
                'parked' if status == 'parked' else
                'compile_blocked' if compiled is False else
                'partial' if compiled is True else
                'untouched' if not jobs and node.get('attempt_id') is None else 'pending')
    size = node.get('size')
    known_size = isinstance(size, int) and not isinstance(size, bool) and size > 0
    row = dict(name=name, size=size if known_size else None, address=node.get('address'),
               instructions=node.get('instruction_count'), group=group, status=status,
               category=category, score=node.get('score'), compiled=compiled,
               attempt_id=node.get('attempt_id'), work_items=len(jobs))
    semantic = node.get('semantic_validation') or {}
    detail = dict(row, compiler_error=str(residual.get('compiler_error_signature') or '')[:1500],
        differences=(residual.get('first_difference') or [])[:16],
        faults=residual.get('faults') or {},
        instruction_delta=residual.get('instruction_delta'),
        text_length_delta=residual.get('text_length_delta'),
        semantic_status=semantic.get('status'),
        coverage=semantic.get('target_coverage') or {},
        debt=[str(x)[:500] for x in (semantic.get('debt') or [])[:8]],
        recent_work=[{k:j.get(k) for k in ('profile','status','lane','model')} for j in jobs[-6:][::-1]])
    # Coverage reports can contain entire instruction inventories; expose totals only.
    detail['coverage'] = {k:detail['coverage'][k] for k in
        ('instruction_coverage','branch_edge_coverage','covered_instruction_count',
         'reachable_instruction_count','covered_branch_edge_count','conditional_branch_count')
        if k in detail['coverage']}
    detail['differences'] = [str(x)[:500] for x in detail['differences']]
    return row, detail


class MapFeed:
    def __init__(self, run):
        self.run = Path(run)
        self.lock = threading.Lock()
        self.last = 0
        self.commit = None
        self.refs = {}
        self.rows = {}
        self.details = {}
        self.groups = {}
        self.payload = None

    def refresh(self):
        if self.payload is not None and time.monotonic() - self.last < 5:
            return
        pointer = json.loads((self.run / 'campaign.json').read_bytes())
        if pointer.get('kind') != 'campaign-checkpoint-index-v1':
            raise ValueError('map requires a compact campaign checkpoint')
        if pointer['commit'] == self.commit:
            self.last = time.monotonic()
            return
        store = (self.run / pointer['store']).resolve()
        if store.parent != self.run.resolve():
            raise ValueError('checkpoint store must be in the run directory')
        # Copy compressed rows, then release SQLite before decompression/projection.
        # This is the checkpoint store, never the attempt database.
        with closing(sqlite3.connect('file:' + quote(store.as_posix(), safe='/:') + '?mode=ro',
                             uri=True, timeout=2)) as conn:
            row = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != pointer['sha256']:
                raise ValueError('checkpoint manifest missing or corrupt')
            manifest = json.loads(row[0])
            needed = {manifest['metadata']} | {key for name,key in manifest['nodes'].items()
                                               if self.refs.get(name) != key}
            compressed = conn.execute('SELECT hash,payload FROM objects WHERE hash IN '
                '(SELECT value FROM json_each(?))', (json.dumps(sorted(needed)),)).fetchall()
        objects = {}
        for key, blob in compressed:
            raw = zlib.decompress(blob)
            if hashlib.sha256(raw).hexdigest() != key:
                raise ValueError('checkpoint object checksum mismatch')
            objects[key] = json.loads(raw)
        if set(objects) != needed:
            raise ValueError('checkpoint object missing')
        metadata = objects[manifest['metadata']]
        groups = metadata.get('tu_index') or {}
        rows, details = {}, {}
        for name, key in manifest['nodes'].items():
            group = groups.get(name, 'Ungrouped')
            if self.refs.get(name) == key:
                rows[name], details[name] = dict(self.rows[name]), dict(self.details[name])
                rows[name]['group'] = details[name]['group'] = group
            else:
                rows[name], details[name] = project(name, objects[key], group)
        functions = sorted(rows.values(), key=lambda r: (r['address'] or 0, r['name']))
        active = [j.get('function') for j in (pointer.get('health') or {}).get('parallel_inflight', [])]
        payload = dict(commit=pointer['commit'], updated_at=pointer.get('updated_at'),
            functions=functions, active=active,
            total_bytes=sum(r['size'] or 0 for r in functions),
            exact_bytes=sum(r['size'] or 0 for r in functions if r['category']=='exact'),
            unknown_sizes=sum(r['size'] is None for r in functions),
            group_count=len({r['group'] for r in functions}))
        self.rows, self.details, self.refs = rows, details, manifest['nodes']
        self.payload, self.commit, self.last = payload, pointer['commit'], time.monotonic()

    def get(self, name=None):
        with self.lock:
            self.refresh()
            if name is None:
                return self.payload
            if name not in self.details:
                raise KeyError(name)
            return dict(self.details[name], commit=self.commit)

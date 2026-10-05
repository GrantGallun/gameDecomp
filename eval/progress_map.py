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
from eval import progress_integration, progress_runtime
from solver.evidence_schedule import fingerprint


# Parked for what the function IS, not for a gap in the pipeline: CPU/hardware
# internals and hand-written SDK assembly. These stay assembly in a green build
# by design, so the map shows them apart from functions a pipeline fix unparks.
ASSEMBLY_BLOCKERS = frozenset({'hardware_backend_required',
                               'sdk_control_flow_or_relocation_unsupported'})


def project(name, node, group):
    residual = node.get('residual') or {}
    status = node.get('status', 'unknown')
    compiled = residual.get('compiled')
    jobs = node.get('jobs') or []
    blocker = node.get('blocker') if isinstance(node.get('blocker'), dict) else {}
    category = ('exact' if status == 'object_exact' else
                'rom_verified' if status == 'integrated' else
                'function_exact' if status == 'function_exact_pending_integration' else
                ('parked_asm' if blocker.get('status') in ASSEMBLY_BLOCKERS else 'parked')
                if status == 'parked' else
                'compile_blocked' if compiled is False else
                'partial' if compiled is True else
                'untouched' if not jobs and node.get('attempt_id') is None else 'pending')
    size = node.get('size')
    known_size = isinstance(size, int) and not isinstance(size, bool) and size > 0
    row = dict(name=name, size=size if known_size else None, address=node.get('address'),
               instructions=node.get('instruction_count'), group=group, status=status,
               category=category, score=node.get('score'), compiled=compiled,
               instruction_distance=residual.get('instruction_distance'),
               register_distance=residual.get('register_distance'),
               attempt_id=node.get('attempt_id'), work_items=len(jobs))
    semantic = node.get('semantic_validation') or {}
    detail = dict(row, source_sha256=node.get('source_sha256'),
        verification_sha256=fingerprint(node.get('verification', {})),
        compiler_error=str(residual.get('compiler_error_signature') or '')[:1500],
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
        self.integration_sweep = None
        self.runtime_capture = None
        self.binary_data_catalog = None

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
        self.integration_sweep = metadata.get('integration_sweep')
        self.runtime_capture = metadata.get('runtime_capture')
        self.binary_data_catalog = metadata.get('binary_data_catalog')
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

    def integration(self):
        with self.lock:
            self.refresh()
            return progress_integration.project(self.integration_sweep,
                self.details, self.run, self.commit)

    def data(self):
        """Project recorded summary metadata without reopening catalog files."""
        with self.lock:
            self.refresh()
            record = self.binary_data_catalog
            if not isinstance(record, dict) or not isinstance(record.get('summary'), dict):
                return {'status': 'not_built', 'commit': self.commit, 'summary': {},
                        'scope': 'No binary data catalog recorded in this checkpoint.'}
            fields = ('regions', 'rom_verified_bytes', 'rom_file_bytes',
                      'reconstructed_bytes', 'reconstructed_rom_file_bytes',
                      'readonly_initial_bytes', 'mutable_initial_bytes', 'unbacked_bss_bytes',
                      'typed_c_verified_bytes', 'address_references',
                      'functions_with_data_context', 'functions_with_readonly_inputs',
                      'rom_bytes', 'omitted_regions', 'omitted_bytes', 'unparsed_directives')
            summary = {k: v for k in fields if isinstance((v := record['summary'].get(k)), int)
                       and not isinstance(v, bool) and v >= 0}
            hashes = {k: str(record.get(k) or '') for k in ('catalog_sha256', 'input_sha256', 'sha256')}
            valid = all(len(value) == 64 and all(c in '0123456789abcdef' for c in value)
                        for value in hashes.values())
            return {'status': 'recorded' if valid else 'unavailable', 'commit': self.commit,
                    'updated_at': self.payload.get('updated_at'), 'summary': summary if valid else {},
                    'hashes': hashes if valid else {},
                    'scope': 'Pinned named data spans; ROM bytes, data directives, BSS and typed C are separate measures.'}

    def integration_artifact(self, digest):
        with self.lock:
            self.refresh()
            latest = (self.integration_sweep or {}).get('latest') or {}
            previous = (self.integration_sweep or {}).get('latest_success') or {}
            matches = {(row.get('path'), row.get('kind')): row
                       for row in [*(latest.get('artifacts') or []), *(previous.get('artifacts') or [])]
                       if row.get('sha256') == digest and row.get('kind') in {'receipt', 'log'}}
            if not matches:
                raise KeyError('Unknown integration artifact')
            # Repeated builds can archive byte-identical logs at different
            # paths. Their hash identifies the same downloadable content.
            row = next(iter(matches.values()))
            return progress_integration.read_artifact(self.run, row), row['kind']

    def runtime(self):
        with self.lock:
            self.refresh()
            return progress_runtime.project(self.runtime_capture,
                self.details, self.run, self.commit)

    def runtime_artifact(self, digest):
        with self.lock:
            self.refresh()
            latest = (self.runtime_capture or {}).get('latest') or {}
            matches = [row for row in latest.get('artifacts') or []
                       if row.get('sha256') == digest and row.get('kind') in {'receipt', 'log'}]
            if not matches:
                raise KeyError('Unknown runtime capture artifact')
            row = matches[0]
            return progress_integration.read_artifact(self.run, row), row['kind']

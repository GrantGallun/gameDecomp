"""Freeze task inputs and detect drift before spending compiler budget."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import math

ASSISTANCE = {'synthetic', 'header_assisted', 'recovered', 'declared_unassisted', 'unknown'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def inside(root, name):
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError('artifact path escapes bundle')
    return path


def freeze(config: dict, destination: Path, *, identity: dict) -> dict:
    tasks = config.get('tasks', [])
    if not tasks:
        raise ValueError('at least one task required')
    seen = set()
    for task in tasks:
        name = task.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', name):
            raise ValueError('invalid task id')
        if name in seen:
            raise ValueError('duplicate task id')
        seen.add(name)
        if not re.fullmatch(r'[A-Za-z_]\w*', task.get('function', '')):
            raise ValueError('invalid function')
        if not task.get('compile_target', '').startswith('build/'):
            raise ValueError('compile_target must identify a build object')
        if task.get('assistance', 'unknown') not in ASSISTANCE:
            raise ValueError('unsupported assistance label')
        for proposal in task.get('proposals', []):
            if proposal.get('assistance', 'unknown') not in ASSISTANCE:
                raise ValueError('unsupported proposal assistance label')
            seconds = proposal.get('generation_seconds', 0)
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError('invalid recorded generation_seconds')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    frozen, files = [], {}

    def capture(original, relative):
        data = Path(original).read_bytes()
        path = inside(destination, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[relative] = digest(data)
        return relative

    for task in tasks:
        name = task['id']
        row = {k: task[k] for k in ('id', 'function', 'compile_target')}
        row.update(assistance=task.get('assistance', 'unknown'), cluster=task.get('cluster', name),
                   provenance=task.get('provenance', {}), accesses=task.get('accesses', []),
                   flows=task.get('flows', []))
        row['source'] = capture(task['source'], f'inputs/{name}/source.c')
        row['target_object'] = capture(task['target_object'], f'inputs/{name}/target.o')
        row['context'] = f'inputs/{name}/context'
        inside(destination, row['context']).mkdir()
        if task.get('context'):
            origin = Path(task['context']).resolve()
            for path in sorted(origin.rglob('*')):
                if path.is_file() and path.suffix in {'.h', '.inc'}:
                    if not path.resolve().is_relative_to(origin):
                        raise ValueError('context symlink escapes input directory')
                    capture(path, row['context'] + '/' + path.relative_to(origin).as_posix())
        row['proposals'] = []
        for i, proposal in enumerate(task.get('proposals', [])):
            item = {k: v for k, v in proposal.items() if k != 'source'}
            item['assistance'] = proposal.get('assistance', 'synthetic' if row['assistance'] == 'synthetic' else 'unknown')
            item['source'] = capture(proposal['source'], f'inputs/{name}/proposal-{i}.c')
            row['proposals'].append(item)
        frozen.append(row)
    payload = {'schema_version': 1, 'kind': 'research-suite-inputs', 'tasks': frozen,
               'identity': identity, 'files': files, 'settings': config.get('settings', {})}
    write_json(destination / 'manifest.json', {'payload': payload, 'sha256': fingerprint(payload)})
    return payload


def load(root: Path) -> dict:
    root = Path(root)
    envelope = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    payload = envelope['payload']
    if payload.get('schema_version') != 1 or envelope.get('sha256') != fingerprint(payload):
        raise ValueError('manifest hash or schema mismatch')
    for name, expected in payload['files'].items():
        path = inside(root, name)
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f'artifact hash mismatch: {name}')
    return payload


def task_source(root: Path, task: dict) -> str:
    return inside(root, task['source']).read_text(encoding='utf-8')

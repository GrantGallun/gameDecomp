"""Run a frozen direct compiler probe in a private WSL repository and database.

Manifest fields: repo, kb, native_db, function, addr, native_attempt_id,
source_sha256, proposals[{label, source, source_sha256}]. No reference C input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('direct_compiler_benchmark',
                                             HERE.parent / 'compiler-effects-20260926' / 'benchmark.py')
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)

from eval.campaign_workers import isolate
from solver import workspace


def read_manifest(path: Path, data: bytes | None = None) -> dict:
    manifest = json.loads(data if data is not None else path.read_bytes())
    required = ('repo', 'kb', 'native_db', 'function', 'addr', 'native_attempt_id',
                'source_sha256', 'expected_baseline_score', 'proposals')
    if any(key not in manifest for key in required) or not manifest['proposals']:
        raise ValueError('incomplete manifest')
    for key in ('repo', 'kb', 'native_db'):
        if not PurePosixPath(manifest[key]).is_absolute() or not str(manifest[key]).startswith('/home/'):
            raise ValueError(f'{key} must be a native WSL /home path')
    seen_labels, seen_sources = set(), {manifest['source_sha256']}
    for proposal in manifest['proposals']:
        if benchmark.digest(proposal['source']) != proposal['source_sha256']:
            raise ValueError('proposal source hash mismatch')
        if not proposal['label'] or not proposal['label'].replace('-', '').replace('_', '').isalnum():
            raise ValueError('proposal label must be a simple name')
        if proposal['label'] in seen_labels or proposal['source_sha256'] in seen_sources:
            raise ValueError('duplicate proposal label or source')
        seen_labels.add(proposal['label'])
        seen_sources.add(proposal['source_sha256'])
    return manifest


def compare(parent: dict, child: dict) -> dict:
    return {'label': child['label'], 'parent_source_sha256': parent['source_sha256'],
            'child_source_sha256': child['source_sha256'],
            'parent_receipt_id': parent['receipt_id'], 'child_receipt_id': child['receipt_id'],
            'parent_score': parent['score'], 'child_score': child['score'],
            'score_delta': round(child['score'] - parent['score'], 6),
            'compiled': child['compiled'], 'frontend_passed': child['frontend_passed'],
            'object_exact': child['exact'] and child['certificate_exact'] is True
                            and child['frontend_passed'] is True,
            'elapsed_seconds': child['elapsed_seconds'],
            'assembly_equal': (parent['asm'] == child['asm']) if child['compiled'] else None}


def score_one(conn, ws: Path, repo: Path, root: dict, source: str,
              tag: str, parent_id: int, action: str) -> dict:
    started = time.perf_counter()
    args = {'strategy': 'direct-compiler:' + action, 'model': 'deterministic',
            'run_id': 'direct-compiler-20260926:' + root['function'],
            'parent_attempt_id': parent_id, 'relation': 'direct-compiler-probe',
            'action': action, 'extra': {'training_eligible': False,
                      'native_parent_attempt_id': root['native_attempt_id'],
                      'frozen_source_sha256': benchmark.digest(source)}}
    try:
        attempt = workspace.score(ws, repo, tag, source, conn=conn,
                                  func=root['function'], **args)
    except Exception as exc:
        attempt = workspace.Attempt(False, 0.0, False, '',
                                    f'{type(exc).__name__}: {exc}', '')
        workspace.record_attempt(conn, root['function'], source, attempt,
                                 **{**args, 'extra': {**args['extra'], 'infrastructure_failure': True}})
    assembly = benchmark.assembly(ws / f'{tag}_object_dump_normalized.s') if attempt.compiled else None
    return {'receipt_id': attempt.receipt_id, 'compiled': attempt.compiled,
            'score': attempt.score, 'exact': attempt.exact,
            'frontend_passed': (attempt.frontend or {}).get('passed'),
            'certificate_exact': (attempt.verification or {}).get('exact'),
            'verification': attempt.verification,
            'source_sha256': benchmark.digest(source), 'error': attempt.compiler_stderr,
            'elapsed_seconds': time.perf_counter() - started,
            'asm': assembly, 'diff': attempt.diff}


def raw_once(path: Path, value: str) -> None:
    with path.open('x') as stream:
        stream.write(value)


def save_artifacts(folder: Path, label: str, source: str, receipt: dict) -> None:
    folder.mkdir(exist_ok=True)
    raw_once(folder / f'{label}.c', source)
    if receipt['asm'] is not None:
        raw_once(folder / f'{label}.s', receipt['asm'])
    raw_once(folder / f'{label}.diff', receipt['diff'])
    benchmark.write_once(folder / f'{label}.certificate.json',
                         {'source_sha256': receipt['source_sha256'],
                          'receipt_id': receipt['receipt_id'],
                          'verification': receipt['verification'],
                          'frontend_passed': receipt['frontend_passed'], 'error': receipt['error']})


def run(manifest_path: Path, private: Path) -> dict:
    started = time.perf_counter()
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = benchmark.digest(manifest_bytes)
    manifest = read_manifest(manifest_path, manifest_bytes)
    if private.exists() or (HERE / 'report.json').exists():
        raise FileExistsError('private probe or report already exists; use a new experiment path')
    if not str(private).startswith('/home/'):
        raise ValueError('private workspace must be on the WSL filesystem')
    with benchmark.db_ro(Path(manifest['native_db'])) as native:
        row = native.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?',
                             (manifest['native_attempt_id'],)).fetchone()
    if row is None or row[1] != manifest['source_sha256'] or benchmark.digest(row[0]) != row[1]:
        raise ValueError('native parent receipt/source mismatch')
    source = row[0]
    private.mkdir(parents=True)
    root = {**manifest, 'native_attempt_id': manifest['native_attempt_id']}
    conn = benchmark.clone_lineage(private / 'attempts.sqlite', root, manifest)
    try:
        conn.execute('INSERT INTO attempt_edges SELECT * FROM native.attempt_edges '
                     'WHERE child_attempt_id IN (SELECT id FROM attempts)')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('copied ancestry failed foreign-key check')
        conn.commit()
        repo = isolate(Path(manifest['repo']), private / 'repo', manifest['function'])
        ws = repo / 'nonmatchings' / manifest['function']
        folder = HERE / 'artifacts'
        tag = manifest['function'] + '_direct_parent'
        parent = score_one(conn, ws, repo, root, source, tag,
                           manifest['native_attempt_id'], 'baseline')
        save_artifacts(folder, 'baseline', source, parent)
        if (not parent['compiled'] or parent['frontend_passed'] is not True or
                abs(parent['score'] - manifest['expected_baseline_score']) > .002):
            raise RuntimeError('private baseline failed compile/frontend/score reproduction')
        children = []
        for proposal in manifest['proposals']:
            tag = manifest['function'] + '_direct_' + proposal['label']
            child = score_one(conn, ws, repo, root, proposal['source'], tag,
                              parent['receipt_id'], proposal['label'])
            child['label'] = proposal['label']
            save_artifacts(folder, proposal['label'], proposal['source'], child)
            children.append(child)
        report = {'kind': 'direct-compiler-paired-probe-v1',
                  'manifest_sha256': manifest_sha,
                  'function': manifest['function'], 'native_parent_attempt_id': manifest['native_attempt_id'],
                  'private_db': str(private / 'attempts.sqlite'),
                  'artifacts_dir': str(folder),
                  'baseline': {k: parent[k] for k in ('receipt_id', 'source_sha256', 'compiled',
                              'score', 'exact', 'frontend_passed', 'certificate_exact',
                              'elapsed_seconds', 'error')},
                  'comparisons': [compare(parent, c) for c in children],
                  'elapsed_seconds': time.perf_counter() - started,
                  'training_eligible': False}
        benchmark.write_once(HERE / 'report.json', report)
        return report
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--private', type=Path,
                        default=Path('/home/grant/decomp/experiments/direct-compiler-20260926/probes'))
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.private), indent=2))

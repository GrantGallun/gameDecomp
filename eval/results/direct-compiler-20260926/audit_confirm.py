"""Audit the frozen direct probe, check raw-ledger novelty, and confirm its exact C."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('direct_compiler_harness', HERE / 'harness.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)
from eval.campaign_workers import isolate
from solver import regalloc_mutations, workspace


def audit_receipt(db, expected: dict, function: str, native_parent_id: int) -> dict:
    row = db.execute('SELECT id,parent_attempt_id,source_code,source_sha256,compiled,score,exact,'
                     'strategy,model,run_id,sampling FROM attempts WHERE id=?',
                     (expected['receipt_id'],)).fetchone()
    if row is None or row[1] != expected['parent_id'] or row[3] != expected['source_sha256']:
        raise ValueError('receipt/source/parent mismatch')
    if harness.benchmark.digest(row[2]) != row[3]:
        raise ValueError('receipt source bytes mismatch')
    if (bool(row[4]), bool(row[6])) != (expected['compiled'], expected['exact']) or abs(row[5] - expected['score']) > 1e-6:
        raise ValueError('receipt score/status mismatch')
    if (row[7], row[8], row[9]) != ('direct-compiler:' + expected['action'], 'deterministic',
                                   'direct-compiler-20260926:' + function):
        raise ValueError('receipt experiment metadata mismatch')
    edge = db.execute('SELECT parent_attempt_id,relation,action FROM attempt_edges '
                      'WHERE child_attempt_id=?', (row[0],)).fetchall()
    if edge != [(expected['parent_id'], 'direct-compiler-probe', expected['action'])]:
        raise ValueError('receipt edge mismatch')
    sampling = json.loads(row[10] or '{}')
    if (sampling.get('training_eligible') is not False or
            sampling.get('native_parent_attempt_id') != native_parent_id or
            sampling.get('frozen_source_sha256') != row[3] or
            (sampling.get('frontend') or {}).get('passed') != expected['frontend_passed'] or
            (sampling.get('verification') or {}).get('exact') != expected['certificate_exact']):
        raise ValueError('receipt evidence mismatch')
    return {'receipt_id': row[0], 'source_sha256': row[3], 'compiled': bool(row[4]),
            'score': row[5], 'exact': bool(row[6])}


def exact_metadata(path: Path, function: str) -> list[dict]:
    """Read raw exact IDs and hashes only; never select source_code."""
    with harness.benchmark.db_ro(path) as db:
        return [{'id': row[0], 'source_sha256': row[1], 'compiled': bool(row[2])}
                for row in db.execute('SELECT a.id,a.source_sha256,a.compiled FROM attempts a '
                                      'JOIN functions f ON f.addr=a.func_addr '
                                      'WHERE f.name=? AND a.exact=1 ORDER BY a.id', (function,))]


def artifact_certificate(path: Path, receipt_id: int, source_sha: str,
                         frontend_passed: bool | None) -> bool | None:
    artifact = json.loads(path.read_text())
    if (artifact['receipt_id'] != receipt_id or artifact['source_sha256'] != source_sha or
            artifact['frontend_passed'] != frontend_passed):
        raise ValueError('certificate artifact identity mismatch')
    return (artifact['verification'] or {}).get('exact')


def audit_pool(manifest: dict, report: dict, db_path: Path) -> dict:
    if (report['function'] != manifest['function'] or
            report['native_parent_attempt_id'] != manifest['native_attempt_id'] or
            len(report['comparisons']) != len(manifest['proposals'])):
        raise ValueError('report cohort mismatch')
    by_label = {p['label']: p for p in manifest['proposals']}
    if set(by_label) != {c['label'] for c in report['comparisons']}:
        raise ValueError('report labels mismatch')
    baseline = report['baseline']
    folder = Path(report['artifacts_dir'])
    baseline_certificate = artifact_certificate(folder / 'baseline.certificate.json',
        baseline['receipt_id'], manifest['source_sha256'], baseline['frontend_passed'])
    if baseline_certificate != baseline['certificate_exact']:
        raise ValueError('baseline certificate artifact mismatch')
    wanted = [{'receipt_id': baseline['receipt_id'], 'parent_id': manifest['native_attempt_id'],
               'source_sha256': manifest['source_sha256'], 'compiled': baseline['compiled'],
               'score': baseline['score'], 'exact': baseline['exact'],
               'frontend_passed': baseline['frontend_passed'],
               'certificate_exact': baseline['certificate_exact'], 'action': 'baseline'}]
    for child in report['comparisons']:
        proposal = by_label[child['label']]
        if (child['parent_receipt_id'] != baseline['receipt_id'] or
                child['child_source_sha256'] != proposal['source_sha256'] or
                child['parent_source_sha256'] != manifest['source_sha256']):
            raise ValueError('comparison source/parent mismatch')
        certificate = artifact_certificate(folder / f"{child['label']}.certificate.json",
            child['child_receipt_id'], proposal['source_sha256'], child['frontend_passed'])
        if child['object_exact'] and certificate is not True:
            raise ValueError('claimed exact lacks certificate artifact')
        wanted.append({'receipt_id': child['child_receipt_id'], 'parent_id': baseline['receipt_id'],
                       'source_sha256': proposal['source_sha256'], 'compiled': child['compiled'],
                       'score': child['child_score'], 'exact': child['object_exact'],
                       'frontend_passed': child['frontend_passed'],
                       'certificate_exact': certificate, 'action': child['label']})
    with harness.benchmark.db_ro(db_path) as db:
        ids = {row[0] for row in db.execute("SELECT id FROM attempts WHERE strategy LIKE 'direct-compiler:%'")}
        if ids != {row['receipt_id'] for row in wanted}:
            raise ValueError('unexpected direct probe receipt count/IDs')
        audited = [audit_receipt(db, row, manifest['function'], manifest['native_attempt_id'])
                   for row in wanted]
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('private database foreign key failure')
    return {'receipt_count': len(audited), 'compiled_count': sum(r['compiled'] for r in audited),
            'exact_count': sum(r['exact'] for r in audited),
            'receipts': audited}


def stream_membership(db_path: Path, manifest: dict, winner_sha: str) -> dict:
    with harness.benchmark.db_ro(db_path) as db:
        row = db.execute('SELECT source_code,source_sha256,diff_summary,sampling FROM attempts WHERE id=?',
                         (manifest['native_attempt_id'],)).fetchone()
    if row is None or row[1] != manifest['source_sha256'] or harness.benchmark.digest(row[0]) != row[1]:
        raise ValueError('baseline stream source mismatch')
    matches, count = [], 0
    for label, family, source in regalloc_mutations.variants(
            row[0], manifest['function'], row[2] or '', evidence=json.loads(row[3] or '{}')):
        count += 1
        if harness.benchmark.digest(source) == winner_sha:
            matches.append({'ordinal': count, 'label': label, 'family': family})
    return {'stream_emissions': count, 'winning_source_already_emitted': bool(matches),
            'matching_emissions': matches}


def confirm(manifest: dict, report: dict, db_path: Path, private: Path) -> dict:
    winners = [c for c in report['comparisons'] if c['object_exact']]
    if len(winners) != 1:
        raise ValueError('expected exactly one exact frozen proposal')
    winner = winners[0]
    proposal = next(p for p in manifest['proposals'] if p['label'] == winner['label'])
    source = proposal['source']
    if harness.benchmark.digest(source) != winner['child_source_sha256']:
        raise ValueError('winner source hash mismatch')
    if private.exists():
        raise FileExistsError(private)
    private.mkdir(parents=True)
    confirm_db = private / 'attempts.sqlite'
    with harness.benchmark.db_ro(db_path) as src, sqlite3.connect(confirm_db) as dst:
        src.backup(dst)
    repo = isolate(Path(manifest['repo']), private / 'repo', manifest['function'])
    ws = repo / 'nonmatchings' / manifest['function']
    tag = manifest['function'] + '_direct_independent_confirmation'
    started = time.perf_counter()
    args = {'strategy': 'direct-compiler:independent-confirmation', 'model': 'deterministic',
            'run_id': 'direct-compiler-20260926:confirmation',
            'parent_attempt_id': report['baseline']['receipt_id'],
            'relation': 'independent-confirmation', 'action': winner['label'],
            'extra': {'training_eligible': False, 'frozen_source_sha256': winner['child_source_sha256'],
                      'original_exact_receipt_id': winner['child_receipt_id']}}
    with sqlite3.connect(confirm_db) as db:
        try:
            attempt = workspace.score(ws, repo, tag, source, conn=db,
                                      func=manifest['function'], **args)
        except Exception as exc:
            attempt = workspace.Attempt(False, 0., False, '', f'{type(exc).__name__}: {exc}', '')
            workspace.record_attempt(db, manifest['function'], source, attempt,
                                     **{**args, 'extra': {**args['extra'], 'infrastructure_failure': True}})
        row = db.execute('SELECT source_sha256,compiled,exact FROM attempts WHERE id=?',
                         (attempt.receipt_id,)).fetchone()
        edge = db.execute('SELECT parent_attempt_id,relation,action FROM attempt_edges '
                          'WHERE child_attempt_id=?', (attempt.receipt_id,)).fetchall()
    passed = (attempt.compiled and workspace.repair_complete(attempt) and
              (attempt.frontend or {}).get('passed') is True and
              (attempt.verification or {}).get('exact') is True and
              row == (winner['child_source_sha256'], 1, 1) and
              edge == [(report['baseline']['receipt_id'], 'independent-confirmation', winner['label'])])
    asm = ws / f'{tag}_object_dump_normalized.s'
    if asm.is_file():
        harness.raw_once(HERE / 'confirmation.s', asm.read_text())
    harness.raw_once(HERE / 'confirmation.c', source)
    result = {'function': manifest['function'], 'source_sha256': winner['child_source_sha256'],
              'original_exact_receipt_id': winner['child_receipt_id'],
              'confirmation_receipt_id': attempt.receipt_id,
              'parent_receipt_id': report['baseline']['receipt_id'],
              'private_db': str(confirm_db), 'compiled': attempt.compiled,
              'frontend_passed': (attempt.frontend or {}).get('passed'),
              'certificate': attempt.verification, 'score': attempt.score,
              'elapsed_seconds': time.perf_counter() - started, 'confirmed': bool(passed)}
    harness.benchmark.write_once(HERE / 'confirmation.json', result)
    harness.benchmark.write_once(HERE / 'confirmation.certificate.json',
                                 {'source_sha256': winner['child_source_sha256'],
                                  'receipt_id': attempt.receipt_id,
                                  'verification': attempt.verification,
                                  'frontend': attempt.frontend})
    if not passed:
        raise RuntimeError('independent ordinary confirmation failed; receipt retained')
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private', type=Path,
                        default=Path('/home/grant/decomp/experiments/direct-compiler-20260926/probes'))
    parser.add_argument('--confirm-private', type=Path,
                        default=Path('/home/grant/decomp/experiments/direct-compiler-20260926/confirmation'))
    parser.add_argument('--research-db', type=Path, default=Path('/home/grant/decomp/kb-sbk1.sqlite'))
    args = parser.parse_args()
    manifest_path = HERE / 'manifest.json'
    report = json.loads((HERE / 'report.json').read_text())
    manifest = harness.read_manifest(manifest_path)
    if report['manifest_sha256'] != harness.benchmark.digest(manifest_path.read_bytes()):
        raise ValueError('report manifest hash mismatch')
    db_path = args.private / 'attempts.sqlite'
    audited = audit_pool(manifest, report, db_path)
    winner = next(c for c in report['comparisons'] if c['object_exact'])
    prior = {'native_campaign': exact_metadata(Path(manifest['native_db']), manifest['function']),
             'research': exact_metadata(args.research_db, manifest['function'])}
    novelty = {'native_campaign': prior['native_campaign'], 'research': prior['research'],
               'same_source_sha256_seen': any(r['source_sha256'] == winner['child_source_sha256']
                                              for rows in prior.values() for r in rows)}
    stream = stream_membership(db_path, manifest, winner['child_source_sha256'])
    harness.benchmark.write_once(HERE / 'audit.json',
                                 {**audited, 'manifest_sha256': report['manifest_sha256'],
                                  'novelty': novelty, 'existing_mutation_stream': stream})
    confirmed = confirm(manifest, report, db_path, args.confirm_private)
    print(json.dumps({'audited_receipts': audited['receipt_count'],
                      'confirmed': confirmed['confirmed'],
                      'confirmation_receipt_id': confirmed['confirmation_receipt_id'],
                      'existing_mutation_stream': stream,
                      'same_source_sha256_seen': novelty['same_source_sha256_seen']}, indent=2))


if __name__ == '__main__':
    main()

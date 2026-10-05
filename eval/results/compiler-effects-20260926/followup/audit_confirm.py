"""Audit the sealed second-edit pool, then independently confirm its first exact.

The confirmation uses a fresh private copy of the function attempt DB and a
new compiler tag in the same isolated native workspace. Native/research dedup
queries read exact-attempt metadata only, never source C.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('effect_followup_probe', HERE / 'probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
benchmark = probe.benchmark

from solver import regalloc_signature, workspace


def audited_attempt(row: dict, proposal: dict, parent: dict, db: sqlite3.Connection,
                    manifest_sha: str) -> dict:
    """Check a follow-up receipt and its direct source-parent edge."""
    receipt = db.execute(
        'SELECT id,parent_attempt_id,source_sha256,compiled,score,exact,compiler_stderr,sampling '
        'FROM attempts WHERE id=?', (row['receipt_id'],)).fetchone()
    if receipt is None:
        raise ValueError(f"missing follow-up receipt {row['receipt_id']}")
    identity = (receipt[1], receipt[2], bool(receipt[3]), bool(receipt[5]))
    wanted = (parent['parent_receipt_id'], proposal['source_sha256'],
              bool(row['compiled']), bool(row['exact']))
    if identity != wanted or receipt[0] != row['receipt_id']:
        raise ValueError(f"receipt/source/parent mismatch: {row['receipt_id']}")
    if abs((receipt[4] or 0.) - row['score']) > 1e-6:
        raise ValueError(f"receipt score mismatch: {row['receipt_id']}")
    edge = db.execute('SELECT parent_attempt_id,relation,action FROM attempt_edges '
                      'WHERE child_attempt_id=?', (row['receipt_id'],)).fetchone()
    if edge != (parent['parent_receipt_id'], 'prospective-second-edit', proposal['label']):
        raise ValueError(f"receipt parent edge mismatch: {row['receipt_id']}")
    sampling = json.loads(receipt[7] or '{}')
    if (sampling.get('training_eligible') is not False or
            sampling.get('header_assisted') is not True or
            sampling.get('followup_parent_sha256') != parent['parent_sha256'] or
            sampling.get('frozen_source_sha256') != proposal['source_sha256'] or
            sampling.get('followup_manifest_sha256') != manifest_sha):
        raise ValueError(f"receipt provenance mismatch: {row['receipt_id']}")
    if (sampling.get('frontend') or {}).get('passed') != row['frontend_passed']:
        raise ValueError(f"frontend receipt mismatch: {row['receipt_id']}")
    verification = sampling.get('verification') or {}
    if verification.get('exact') != row['certificate_exact']:
        raise ValueError(f"certificate receipt mismatch: {row['receipt_id']}")
    if not row['compiled'] and not (receipt[6] or row['error']):
        raise ValueError(f"unexplained compile failure: {row['receipt_id']}")
    return {'receipt_id': row['receipt_id'], 'compiled': row['compiled'],
            'frontend_passed': row['frontend_passed'], 'exact': row['exact'],
            'source_sha256': proposal['source_sha256']}


def audit_pool(run: Path, manifest: dict, report: dict) -> dict:
    manifest_sha = benchmark.digest((run / 'followup' / 'manifest.json').read_bytes())
    if report['manifest_sha256'] != manifest_sha:
        raise ValueError('second-edit report not bound to sealed manifest')
    audited = []
    paths = []
    for root in manifest['roots']:
        function = root['function']
        result_path = run / 'followup' / 'results' / f'{function}.json'
        result = json.loads(result_path.read_text())
        if result['parent_receipt_id'] != root['parent_receipt_id'] or len(result['children']) != len(root['proposals']):
            raise ValueError(f'{function}: result count/parent mismatch')
        by_ordinal = {p['ordinal']: p for p in root['proposals']}
        if set(by_ordinal) != {r['ordinal'] for r in result['children']}:
            raise ValueError(f'{function}: proposal ordinal set mismatch')
        db_path = run / 'private' / function / 'attempts.sqlite'
        with benchmark.db_ro(db_path) as db:
            parent = db.execute('SELECT source_sha256 FROM attempts WHERE id=?',
                                (root['parent_receipt_id'],)).fetchone()
            if parent != (root['parent_sha256'],):
                raise ValueError(f'{function}: source parent changed')
            receipts = db.execute('SELECT id FROM attempts WHERE strategy LIKE '
                                  "'compiler-effects-two-step:%'").fetchall()
            if len(receipts) != len(root['proposals']):
                raise ValueError(f'{function}: unexpected follow-up receipt count')
            if {r[0] for r in receipts} != {r['receipt_id'] for r in result['children']}:
                raise ValueError(f'{function}: follow-up receipt IDs differ')
            for child in result['children']:
                proposal = by_ordinal[child['ordinal']]
                if (child['label'], child['family'], child['source_sha256']) != (
                        proposal['label'], proposal['family'], proposal['source_sha256']):
                    raise ValueError(f'{function}: child proposal identity mismatch')
                audited.append(audited_attempt(child, proposal, root, db, manifest_sha))
        paths.append(str(result_path))
    counted = {'attempt_count': len(audited),
               'compiled_count': sum(a['compiled'] for a in audited),
               'frontend_pass_count': sum(a['frontend_passed'] is True for a in audited),
               'failure_count': sum(not a['compiled'] for a in audited),
               'exact_count': sum(a['exact'] and a['frontend_passed'] is True for a in audited)}
    for key in ('attempt_count', 'compiled_count', 'frontend_pass_count'):
        if counted[key] != report[key]:
            raise ValueError(f'follow-up report {key} does not match DB audit')
    if counted['exact_count'] != len(report['exact']):
        raise ValueError('follow-up exact count does not match DB audit')
    if counted['attempt_count'] != 256:
        raise ValueError('expected 256 frozen second-edit receipts')
    return {**counted, 'function_count': len(manifest['roots']),
            'result_files': paths, 'receipt_ids': [a['receipt_id'] for a in audited]}


def exact_metadata(db_path: Path, function: str) -> list[dict]:
    """Read only IDs/hashes from a raw exact ledger; never select source_code."""
    with benchmark.db_ro(db_path) as db:
        return [{'id': row[0], 'source_sha256': row[1], 'compiled': bool(row[2])}
                for row in db.execute(
                    'SELECT a.id,a.source_sha256,a.compiled FROM attempts a '
                    'JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1 ORDER BY a.id',
                    (function,))]


def first_stage_parent(run: Path, function: str, receipt_id: int) -> dict:
    base_manifest = benchmark.read_sealed(run / 'manifest.json')
    root = next(r for r in base_manifest['roots'] if r['function'] == function)
    initial = json.loads((run / 'private' / function / 'result.json').read_text())
    children = json.loads((run / 'private' / function / 'children.json').read_text())['children']
    selected = next(c for c in children if c['receipt_id'] == receipt_id)
    proposal = next(p for p in root['proposals'] if p['ordinal'] == selected['ordinal'])
    return {'baseline_receipt_id': initial['baseline']['receipt_id'],
            'baseline_score': initial['baseline']['score'],
            'baseline_gradient': initial['baseline']['gradient'],
            'first_edit': {'receipt_id': selected['receipt_id'], 'ordinal': selected['ordinal'],
                           'label': proposal['label'], 'family': proposal['family'],
                           'score': selected['score'], 'gradient': selected['gradient'],
                           'source_sha256': selected['source_sha256']}}


def confirmation(run: Path, manifest: dict, exact: dict, research_db: Path,
                 native_db: Path) -> dict:
    function = exact['function']
    root = next(r for r in manifest['roots'] if r['function'] == function)
    result = json.loads((run / 'followup' / 'results' / f'{function}.json').read_text())
    child = next(c for c in result['children'] if c['receipt_id'] == exact['receipt_id'])
    proposal = next(p for p in root['proposals'] if p['ordinal'] == child['ordinal'])
    source_path = probe.frozen_file(run, function, child['ordinal'])
    source = source_path.read_text()
    if benchmark.digest(source) != exact['source_sha256']:
        raise ValueError('exact source differs from frozen proposal')
    prior = {'native_campaign': exact_metadata(native_db, function),
             'research': exact_metadata(research_db, function)}
    existing = any(prior.values())
    confirm_dir = run / 'followup' / 'confirmation'
    confirm_dir.mkdir(exist_ok=False)
    source_db = run / 'private' / function / 'attempts.sqlite'
    confirm_db = confirm_dir / 'attempts.sqlite'
    with benchmark.db_ro(source_db) as src, sqlite3.connect(confirm_db) as dst:
        src.backup(dst)
    repo = run / 'private' / function / 'repo'
    ws = repo / 'nonmatchings' / function
    tag = function + '_ce_followup_independent_confirmation'
    start = time.perf_counter()
    with sqlite3.connect(confirm_db) as db:
        attempt = workspace.score(
            ws, repo, tag, source, conn=db, func=function,
            strategy='compiler-effects-two-step:independent-confirmation',
            model='deterministic-confirmation',
            prompt='Recompile sealed exact source with ordinary scorer; no new candidate selection.',
            run_id='compiler-effects-20260926:followup:confirmation',
            parent_attempt_id=root['parent_receipt_id'], relation='independent-confirmation',
            action=proposal['label'],
            extra={'training_eligible': False, 'header_assisted': True,
                   'original_exact_receipt_id': exact['receipt_id'],
                   'frozen_source_sha256': exact['source_sha256']})
        edge = db.execute('SELECT parent_attempt_id,relation FROM attempt_edges '
                          'WHERE child_attempt_id=?', (attempt.receipt_id,)).fetchone()
        row = db.execute('SELECT source_sha256,compiled,exact,sampling FROM attempts WHERE id=?',
                         (attempt.receipt_id,)).fetchone()
    candidate_asm = ws / f'{tag}_object_dump_normalized.s'
    target_asm = ws / 'target_object_dump_normalized.s'
    gradient = (list(regalloc_signature.compare(target_asm.read_text(), candidate_asm.read_text()).gradient)
                if attempt.compiled and candidate_asm.is_file() and target_asm.is_file() else None)
    passed = (attempt.compiled and workspace.repair_complete(attempt) and
              (attempt.verification or {}).get('exact') is True and
              row[:3] == (exact['source_sha256'], 1, 1) and
              edge == (root['parent_receipt_id'], 'independent-confirmation'))
    result = {'function': function, 'source_sha256': exact['source_sha256'],
              'source_path': str(source_path), 'original_exact_receipt_id': exact['receipt_id'],
              'actual_parent_receipt_id': root['parent_receipt_id'],
              'confirmation_receipt_id': attempt.receipt_id,
              'confirmation_private_db': str(confirm_db), 'compiled': attempt.compiled,
              'frontend_passed': (attempt.frontend or {}).get('passed'),
              'certificate_exact': (attempt.verification or {}).get('exact'),
              'score': attempt.score, 'gradient': gradient,
              'elapsed_seconds': time.perf_counter() - start,
              'confirmed': bool(passed), 'dedup': {
                  'classification': 'recovered_known_function' if existing else 'new_vs_raw_exact_ledgers',
                  'native_campaign': prior['native_campaign'], 'research': prior['research'],
                  'same_source_sha256_seen': any(
                      item['source_sha256'] == exact['source_sha256']
                      for rows in prior.values() for item in rows)}}
    benchmark.write_once(confirm_dir / 'result.json', result)
    if not passed:
        raise RuntimeError('independent ordinary confirmation failed; receipt retained')
    return result


def _copy_sealed(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copyfile(source, destination)
    shutil.copyfile(source.with_name(source.name + '.sha256'),
                    destination.with_name(destination.name + '.sha256'))
    benchmark.read_sealed(destination)


def draft_markdown(audit: dict, steps: list[dict], confirmed: dict, report: dict) -> str:
    lines = [
        '# Compiler-effect second-edit follow-up', '',
        f"The frozen pool contains **{audit['attempt_count']}** proposals across "
        f"**{audit['function_count']}** evaluation functions. "
        f"{audit['compiled_count']} compiled, {audit['frontend_pass_count']} passed the frontend, "
        f"and {audit['failure_count']} failed to compile. "
        f"The ordinary scorer certified **{audit['exact_count']}** exact proposals, "
        f"both for `{confirmed['function']}`. The independent recompile "
        f"{'confirmed' if confirmed['confirmed'] else 'did not confirm'} the first exact source.", '',
        'The first-step pool produced no exact match. This follow-up selected the best frontend-valid '
        'gradient improvement per function, then compiled one frozen 32-proposal full mutation stream '
        'from each selected child. It did not adapt after seeing second-step outcomes.', '',
    ]
    for step in steps:
        first, second = step['first_edit'], step['second_edit']
        lines += [
            f"- `{step['function']}`: baseline score {step['baseline_score']:.3f}, gradient "
            f"`{step['baseline_gradient']}`; first edit `{first['label']}` "
            f"({first['family']}, receipt {first['receipt_id']}) reached {first['score']:.3f}, "
            f"`{first['gradient']}`. Second edit `{second['label']}` "
            f"({second['family']}, receipt {second['receipt_id']}) reached {second['score']:.3f}, "
            f"`{second['gradient']}` and exact certification.",
        ]
    lines += [
        '', f"Confirmation receipt {confirmed['confirmation_receipt_id']} compiled the first winning "
        f"source SHA-256 `{confirmed['source_sha256']}` from the actual selected parent "
        f"receipt {confirmed['actual_parent_receipt_id']}. The frontend passed and the object-section "
        'certificate was exact. This is a private, header-assisted object match; whole-ROM integration '
        'was not tested.', '',
        f"Raw exact-ledger classification: `{confirmed['dedup']['classification']}`. "
        f"Native campaign exact metadata rows: {len(confirmed['dedup']['native_campaign'])}; "
        f"research exact metadata rows: {len(confirmed['dedup']['research'])}. "
        'These raw ledgers do not establish frontend or provenance status.', '',
        'The observed two-edit path shows that a child in the first-step improvement pool can expose '
        'an exact second proposal. The generator labels and before/after gradients describe the edits '
        'and their compiled outcomes; no compiler trace was taken for this path, so they do not establish '
        'a specific register-allocation or scheduling cause.', '',
        f"Frozen manifest: [manifest.json](manifest.json). "
        f"Second-step report: [report.json](report.json). "
        f"Audit: [audit.json](audit.json). "
        f"Confirmation: [confirmation.json](confirmation.json).", '',
        f"Measured compiler time summed across all follow-up attempts: "
        f"{report['wall_seconds_sum']:.1f}s; concurrent stage elapsed "
        f"{report['elapsed_wall_seconds']:.1f}s. One additional ordinary confirmation compile was run.",
        '',
    ]
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path,
                        default=Path('/home/grant/decomp/experiments/compiler-effects-20260926'))
    parser.add_argument('--research-db', type=Path,
                        default=Path('/home/grant/decomp/kb-sbk1.sqlite'))
    args = parser.parse_args()
    run = args.run
    manifest = probe.followup_manifest(run)
    report = benchmark.read_sealed(run / 'followup' / 'report.json')
    audit = audit_pool(run, manifest, report)
    exacts = sorted(report['exact'], key=lambda row: (row['function'], row['receipt_id']))
    if not exacts:
        raise RuntimeError('sealed follow-up report contains no exact source to confirm')
    base_manifest = benchmark.read_sealed(run / 'manifest.json')
    steps = []
    for exact in exacts:
        root = next(r for r in manifest['roots'] if r['function'] == exact['function'])
        child_result = json.loads((run / 'followup' / 'results' / f"{exact['function']}.json").read_text())
        child = next(c for c in child_result['children'] if c['receipt_id'] == exact['receipt_id'])
        proposal = next(p for p in root['proposals'] if p['ordinal'] == child['ordinal'])
        first = first_stage_parent(run, exact['function'], root['parent_receipt_id'])
        steps.append({**first, 'function': exact['function'],
                      'second_edit': {'receipt_id': child['receipt_id'], 'ordinal': child['ordinal'],
                                      'label': proposal['label'], 'family': proposal['family'],
                                      'score': child['score'], 'gradient': child['gradient'],
                                      'source_sha256': child['source_sha256']}})
    audit.update(exact_paths=steps, followup_manifest_sha256=report['manifest_sha256'])
    confirmed = confirmation(run, manifest, exacts[0], args.research_db,
                             Path(base_manifest['native_db']))
    artifact = HERE
    benchmark.write_once(artifact / 'audit.json', audit)
    benchmark.write_once(artifact / 'confirmation.json', confirmed)
    _copy_sealed(run / 'followup' / 'manifest.json', artifact / 'manifest.json')
    _copy_sealed(run / 'followup' / 'report.json', artifact / 'report.json')
    result_text = draft_markdown(audit, steps, confirmed, report)
    with (artifact / 'RESULT.md').open('x', encoding='utf-8') as stream:
        stream.write(result_text)
    print(json.dumps({'audited_attempts': audit['attempt_count'], 'exact_count': audit['exact_count'],
                      'confirmation_receipt': confirmed['confirmation_receipt_id'],
                      'confirmed': confirmed['confirmed'],
                      'dedup': confirmed['dedup']['classification'],
                      'artifact': str(artifact / 'RESULT.md')}, indent=2), flush=True)


if __name__ == '__main__':
    main()

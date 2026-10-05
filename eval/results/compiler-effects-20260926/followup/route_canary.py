"""Run the existing MusAsk register-search route once in private isolation.

This is a delivery canary, not a new generator or adaptive search. It uses the
retained source and normal workspace.score callback with the production
regalloc_search defaults (budget 300, beam 3, depth 4, enable=True). Only native
ancestry and the already logged retained baseline are copied into the private
DB; none of the successful follow-up candidates enter the search input.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('effect_followup_probe', HERE / 'probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
benchmark = probe.benchmark

from eval import completion_campaign
from eval.campaign_workers import isolate
from solver import regalloc_search, workspace

FUNCTION = 'MusAsk'
BUDGET = 300
BEAM = 3
DEPTH = 4


def checkpoint_routing() -> dict:
    """Evaluate the actual queue policy on the pinned pre-experiment node."""
    pointer_path = ROOT / 'eval/results/compiler-effects-20260926/overflow/recovery/stage-29289/campaign.pointer.before.json'
    pointer = json.loads(pointer_path.read_text())
    store = Path('/home/grant/decomp/runs/resume-pipeline-20260908') / pointer['store']
    with benchmark.db_ro(store) as db:
        raw_manifest = db.execute('SELECT manifest FROM commits WHERE id=?',
                                  (pointer['commit'],)).fetchone()[0]
        if hashlib.sha256(raw_manifest).hexdigest() != pointer['sha256']:
            raise ValueError('original checkpoint manifest hash mismatch')
        refs = json.loads(raw_manifest)

        def obj(key):
            raw = zlib.decompress(db.execute('SELECT payload FROM objects WHERE hash=?',
                                            (key,)).fetchone()[0])
            if hashlib.sha256(raw).hexdigest() != key:
                raise ValueError('original checkpoint object hash mismatch')
            return json.loads(raw)

        node = obj(refs['nodes'][FUNCTION])
        state = obj(refs['metadata'])
    if node.get('source_sha256') != '035d2c61fb825d2ffe6165963caf1148756a8e6f267bd93463913a9178a68d8f':
        raise ValueError('original MusAsk checkpoint source changed')
    profile = completion_campaign.scheduled_profile(state, node)
    return {'checkpoint': pointer['commit'],
            'status': node['status'], 'source_sha256': node['source_sha256'],
            'scheduler': state['config'].get('scheduler', 'legacy'),
            'next_profile': {k: profile.get(k) for k in
                             ('name', 'lane', 'regalloc_budget', 'deterministic_budget',
                              'operand_budget', 'evidence_key')} if profile else None,
            'jobs': len(node.get('jobs', []))}


def import_retained_baseline(db: sqlite3.Connection, source_db: Path,
                             receipt_id: int, expected_sha: str) -> None:
    """Copy one logged baseline with its run and edge; no pool children."""
    with benchmark.db_ro(source_db) as original:
        row = original.execute('SELECT parent_attempt_id,source_sha256,run_id FROM attempts '
                               'WHERE id=?', (receipt_id,)).fetchone()
        if row is None or row[1] != expected_sha:
            raise ValueError('original retained baseline receipt/source mismatch')
        if db.execute('SELECT id FROM attempts WHERE id=?', (row[0],)).fetchone() is None:
            raise ValueError('native ancestor of retained baseline missing')
        if row[2]:
            run = original.execute('SELECT * FROM attempt_runs WHERE id=?', (row[2],)).fetchone()
            if run is None:
                raise ValueError('retained baseline run record missing')
            db.execute(f"INSERT OR IGNORE INTO attempt_runs VALUES ({','.join('?' * len(run))})", run)
        attempt = original.execute('SELECT * FROM attempts WHERE id=?', (receipt_id,)).fetchone()
        db.execute(f"INSERT INTO attempts VALUES ({','.join('?' * len(attempt))})", attempt)
        edges = original.execute('SELECT parent_attempt_id,child_attempt_id,relation,action,feedback,created_at '
                                 'FROM attempt_edges WHERE child_attempt_id=?', (receipt_id,)).fetchall()
    if len(edges) != 1 or edges[0][0] != row[0]:
        raise ValueError('retained baseline lineage edge missing')
    db.execute('INSERT INTO attempt_edges VALUES (?,?,?,?,?,?)', edges[0])
    db.commit()
    if db.execute('SELECT count(*) FROM attempts WHERE id>?', (receipt_id,)).fetchone()[0]:
        raise ValueError('canary DB unexpectedly contains later attempts')


def canary(run: Path) -> dict:
    manifest = benchmark.read_sealed(run / 'manifest.json')
    benchmark.check_code_pins(run, manifest)
    routing = checkpoint_routing()
    root = next(r for r in manifest['roots'] if r['function'] == FUNCTION)
    if root['split'] != 'evaluation':
        raise ValueError('MusAsk is not in frozen evaluation split')
    parent_source = benchmark.source_file(run, root).read_text()
    if benchmark.digest(parent_source) != root['source_sha256']:
        raise ValueError('retained source hash changed')
    first_result = json.loads((run / 'private' / FUNCTION / 'result.json').read_text())
    retained_id = first_result['baseline']['receipt_id']
    if first_result['baseline']['source_sha256'] != root['source_sha256']:
        raise ValueError('scored baseline source differs from frozen retained source')
    private = run / 'followup' / 'route-canary'
    if private.exists():
        raise FileExistsError(private)
    private.mkdir()
    repo = isolate(Path(manifest['repo']), private / 'repo', FUNCTION)
    ws = repo / 'nonmatchings' / FUNCTION
    db = benchmark.clone_lineage(private / 'attempts.sqlite', root, manifest)
    import_retained_baseline(db, run / 'private' / FUNCTION / 'attempts.sqlite',
                             retained_id, root['source_sha256'])
    started = time.perf_counter()
    target_dump = ws / 'target_object_dump_normalized.s'
    if not target_dump.is_file():
        raise FileNotFoundError(target_dump)
    source_to_receipt = {root['source_sha256']: retained_id}
    compiled_rows = []

    def compile_with_parent(source: str, label: str, source_parent: str | None):
        parent_sha = benchmark.digest(source_parent if source_parent is not None else parent_source)
        parent_id = source_to_receipt.get(parent_sha)
        if parent_id is None:
            raise RuntimeError(f'uncompiled route parent for {label}')
        ordinal = len(compiled_rows) + 1
        tag = f'{FUNCTION}_route_canary_{ordinal:03d}'
        attempt = workspace.score(
            ws, repo, tag, source, conn=db, func=FUNCTION,
            strategy='compiler-effects-route-canary:regalloc-search', model='zero-model',
            run_id='compiler-effects-20260926:route-canary',
            parent_attempt_id=parent_id, relation='regalloc-search', action=label,
            extra={'training_eligible': False, 'header_assisted': True,
                   'parent_source_sha256': parent_sha})
        if label == 'baseline' and (
                not attempt.compiled or (attempt.frontend or {}).get('passed') is not True or
                abs(attempt.score - first_result['baseline']['score']) > .01):
            raise RuntimeError('stock route baseline did not reproduce retained score/frontend')
        source_sha = benchmark.digest(source)
        if attempt.receipt_id is None:
            raise RuntimeError(f'unlogged route proposal {label}')
        source_to_receipt[source_sha] = attempt.receipt_id
        path = ws / f'{tag}_object_dump_normalized.s'
        dump = path.read_text() if attempt.compiled and path.is_file() else None
        compiled_rows.append({'ordinal': ordinal, 'label': label, 'receipt_id': attempt.receipt_id,
                              'parent_receipt_id': parent_id, 'source_sha256': source_sha,
                              'compiled': attempt.compiled, 'frontend_passed':
                              (attempt.frontend or {}).get('passed'), 'score': attempt.score,
                              'exact': workspace.repair_complete(attempt)})
        evidence = {'compiled': bool(attempt.compiled), 'score': attempt.score,
                    'source_attribution': attempt.source_attribution,
                    'frontend': attempt.frontend, 'compiler_recipe': attempt.compiler_recipe}
        return regalloc_search.Compiled(bool(attempt.compiled), workspace.repair_complete(attempt),
                                        dump, attempt.diff or '', evidence)

    outcome = regalloc_search.search(
        FUNCTION, parent_source, compile_with_parent, target_dump.read_text(),
        budget=BUDGET, beam=BEAM, depth=DEPTH,
        enable=True, compile_with_parent=compile_with_parent)
    order_label = 'stmt_move:6->7'
    order_rows = [row for row in outcome.log if row['label'] == order_label and row['depth'] == 1]
    depth2_from_order = any(row['depth'] >= 2 and row['parent'] == order_label
                            for row in outcome.log)
    exact_rows = [row for row in compiled_rows if row['exact']]
    with db:
        receipt_count = db.execute("SELECT count(*) FROM attempts WHERE strategy LIKE "
                                   "'compiler-effects-route-canary:%'").fetchone()[0]
        edge_count = db.execute('SELECT count(*) FROM attempt_edges WHERE child_attempt_id IN '
                                "(SELECT id FROM attempts WHERE strategy LIKE 'compiler-effects-route-canary:%')").fetchone()[0]
    db.close()
    if receipt_count != len(compiled_rows) or edge_count != receipt_count:
        raise ValueError('route canary receipts/edges incomplete')
    baseline_row = compiled_rows[0]
    if baseline_row['label'] != 'baseline' or outcome.compiles != len(compiled_rows):
        raise ValueError('stock route baseline/compile count mismatch')
    report = {'kind': 'compiler-effects-stock-route-canary-v1', 'function': FUNCTION,
              'original_checkpoint_routing': routing,
              'retained_source_sha256': root['source_sha256'], 'retained_attempt_id': root['native_attempt_id'],
              'imported_baseline_receipt_id': retained_id,
              'fresh_baseline_receipt_id': baseline_row['receipt_id'],
              'fresh_baseline_score': baseline_row['score'], 'budget': BUDGET, 'beam': BEAM,
              'depth': DEPTH, 'enable': True, 'exact': outcome.exact,
              'best_label': outcome.best_label, 'best_gradient': outcome.best_gradient,
              'baseline_gradient': outcome.baseline_gradient, 'proposal_compiles': outcome.compiles,
              'logged_attempts_including_baseline': len(compiled_rows),
              'receipt_count_including_baseline': receipt_count,
              'edge_count': edge_count, 'first_order_edit': order_rows,
              'first_order_edit_retained_to_depth2': depth2_from_order,
              'exact_receipts': [{'receipt_id': row['receipt_id'], 'source_sha256': row['source_sha256'],
                                  'label': row['label']} for row in exact_rows],
              'elapsed_seconds': time.perf_counter() - started,
              'private_db': str(private / 'attempts.sqlite'),
              'training_eligible': False, 'header_assisted': True,
              'log': outcome.log, 'compiled_rows': compiled_rows}
    benchmark.seal(private / 'report.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path,
                        default=Path('/home/grant/decomp/experiments/compiler-effects-20260926'))
    args = parser.parse_args()
    report = canary(args.run)
    print(json.dumps({k: report[k] for k in (
        'exact', 'best_label', 'best_gradient', 'baseline_gradient',
        'proposal_compiles', 'receipt_count_including_baseline',
        'first_order_edit_retained_to_depth2', 'exact_receipts', 'elapsed_seconds')},
                     indent=2), flush=True)


if __name__ == '__main__':
    main()

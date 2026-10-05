"""Test ordinary search from original ancestry with the confirmed rewrite wired in."""
import hashlib
import json
from pathlib import Path
import time

import harness
from eval.campaign_workers import isolate
from solver import regalloc_search, workspace

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/home/grant/decomp/experiments/direct-compiler-20260926/search-canary')

def main():
    frozen = json.loads((HERE / 'manifest.json').read_text())
    # Only original source identity and environment enter search; no pool children.
    root = {key: frozen[key] for key in ('function', 'addr', 'native_attempt_id',
                                       'source_sha256', 'repo', 'kb', 'native_db')}
    del frozen
    code_pins = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
        harness.ROOT / 'solver/branch_defaults.py', harness.ROOT / 'solver/regalloc_mutations.py',
        harness.ROOT / 'solver/regalloc_search.py', harness.ROOT / 'solver/workspace.py',
        HERE / 'search_canary.py')}
    if PRIVATE.exists() or (HERE / 'search-report.json').exists():
        raise RuntimeError('canary is write-once')
    PRIVATE.mkdir(parents=True)
    db = harness.benchmark.clone_lineage(PRIVATE / 'attempts.sqlite', root, root)
    db.execute('INSERT INTO attempt_edges SELECT * FROM native.attempt_edges '
               'WHERE child_attempt_id IN (SELECT id FROM attempts)')
    db.commit()
    source, source_sha = db.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?',
                                   (root['native_attempt_id'],)).fetchone()
    assert source_sha == root['source_sha256'] == harness.benchmark.digest(source)
    assert db.execute('SELECT COUNT(*) FROM attempts WHERE id>?', (root['native_attempt_id'],)).fetchone()[0] == 0
    repo = isolate(Path(root['repo']), PRIVATE / 'repo', root['function'])
    ws = repo / 'nonmatchings' / root['function']
    target = (ws / 'target_object_dump_normalized.s').read_text()
    source_receipts = {source_sha: root['native_attempt_id']}
    rows = []

    def compile_with_parent(code, label, parent_source):
        parent_sha = harness.benchmark.digest(parent_source if parent_source is not None else source)
        parent_id = source_receipts[parent_sha]
        tag = f"{root['function']}_direct_search_{len(rows):03d}"
        attempt = workspace.score(ws, repo, tag, code, conn=db, func=root['function'],
            strategy='direct-compiler-search-canary', model='deterministic',
            run_id='direct-compiler-20260926:search', parent_attempt_id=parent_id,
            relation='regalloc-search', action=label,
            extra={'training_eligible': False, 'header_assisted': True,
                   'parent_source_sha256': parent_sha})
        assert attempt.receipt_id is not None
        if label == 'baseline':
            assert attempt.compiled and (attempt.frontend or {}).get('passed') is True
            assert abs(attempt.score - 99.889) < .002
        actual_sha = harness.benchmark.digest(code)
        source_receipts[actual_sha] = attempt.receipt_id
        exact = workspace.repair_complete(attempt)
        rows.append({'label': label, 'receipt_id': attempt.receipt_id, 'parent_receipt_id': parent_id,
                     'source_sha256': actual_sha, 'compiled': attempt.compiled, 'score': attempt.score,
                     'frontend_passed': (attempt.frontend or {}).get('passed'), 'exact': exact})
        path = ws / f'{tag}_object_dump_normalized.s'
        dump = path.read_text() if attempt.compiled and path.is_file() else None
        return regalloc_search.Compiled(attempt.compiled, exact, dump, attempt.diff or '',
            {'compiled': attempt.compiled, 'score': attempt.score, 'frontend': attempt.frontend,
             'source_attribution': attempt.source_attribution, 'compiler_recipe': attempt.compiler_recipe})

    start = time.perf_counter()
    result = regalloc_search.search(root['function'], source, compile_with_parent, target,
        budget=300, beam=3, depth=4, enable=True, compile_with_parent=compile_with_parent)
    elapsed = time.perf_counter() - start
    for row in rows:
        assert db.execute('SELECT parent_attempt_id FROM attempt_edges WHERE child_attempt_id=?',
                          (row['receipt_id'],)).fetchall() == [(row['parent_receipt_id'],)]
        recorded, text = db.execute('SELECT source_sha256,source_code FROM attempts WHERE id=?',
                                    (row['receipt_id'],)).fetchone()
        assert recorded == row['source_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert result.compiles == len(rows)
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == sha for path, sha in code_pins.items())
    report = {'function': root['function'], 'source_sha256': source_sha,
              'native_parent_attempt_id': root['native_attempt_id'], 'private_db': str(PRIVATE / 'attempts.sqlite'),
              'budget': 300, 'beam': 3, 'depth': 4, 'exact': result.exact,
              'compiles_including_baseline': len(rows), 'elapsed_seconds': elapsed,
              'best_label': result.best_label, 'rows': rows, 'log': result.log,
              'audited_attempts_and_edges': len(rows), 'training_eligible': False,
              'header_assisted': True, 'code_pins': code_pins}
    harness.benchmark.write_once(HERE / 'search-report.json', report)
    print(json.dumps({key: report[key] for key in ('exact', 'best_label', 'compiles_including_baseline',
                                                  'elapsed_seconds', 'audited_attempts_and_edges')}, indent=2))
    db.close()

if __name__ == '__main__':
    main()

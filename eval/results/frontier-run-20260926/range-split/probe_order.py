"""One predeclared, private ordinary-scored assignment-order proposal."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from solver import regalloc_mutations, workspace

HERE = Path(__file__).resolve().parent
assert (HERE / 'PROBE.md').is_file()
assert not (HERE / 'PROBE.json').exists()
NAME = 'drawControllerPakFileDeleteConfirmOptions'
PRIVATE = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace')
REPO = PRIVATE / 'repo'
WS = REPO / 'nonmatchings' / NAME
conn = sqlite3.connect(PRIVATE / 'attempts.sqlite')
source, digest, diff, sampling = conn.execute(
    'SELECT source_code,source_sha256,diff_summary,sampling FROM attempts WHERE id=157772').fetchone()
assert hashlib.sha256(source.encode()).hexdigest() == digest == '24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72'
before = ('if (gControllerPakMenuState.state == 2) {\n'
          '        var_v1 = 0x80;\n        var_t0 = 0x80;')
after = ('if (gControllerPakMenuState.state == 2) {\n'
         '        var_t0 = 0x80;\n        var_v1 = 0x80;')
assert source.count(before) == 1
candidate = source.replace(before, after, 1)
stream = [(i, label, kind) for i, (label, kind, code) in enumerate(
    regalloc_mutations.variants(source, NAME, diff, evidence=json.loads(sampling)), 1)
          if code == candidate]
attempt = workspace.score(WS, REPO, NAME + '_assignment_order_probe', candidate,
    conn=conn, func=NAME, strategy='range-order-probe:first-arm-constant-pair',
    parent_attempt_id=157772, relation='diagnostic-probe',
    action='reverse only first-arm independent 0x80 assignments',
    run_id='range-order-probe-20260926')
assert conn.execute('SELECT parent_attempt_id FROM attempt_edges WHERE child_attempt_id=?',
                    (attempt.receipt_id,)).fetchall() == [(157772,)]
assert not conn.execute('PRAGMA foreign_key_check').fetchall()
result = {'parent_attempt_id':157772, 'receipt_id':attempt.receipt_id,
          'source_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
          'before':before, 'after':after, 'compiled':attempt.compiled,
          'score':attempt.score, 'exact':workspace.repair_complete(attempt),
          'frontend_passed':(attempt.frontend or {}).get('passed'),
          'verification':attempt.verification, 'diff':attempt.diff,
          'compiler_stderr':attempt.compiler_stderr,
          'existing_stream_emissions':stream, 'candidate_compiles':1,
          'private_db':str(PRIVATE / 'attempts.sqlite')}
(HERE / 'PROBE.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k:v for k,v in result.items() if k != 'verification'}, indent=2))
conn.close()

"""Preserve the configuration failure that happened before workspace.score could log."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
work = Path(json.loads((HERE / 'census.json').read_text())['work'])
sys.path.insert(0, str(work / 'code'))
from solver import workspace

root = next(r for r in json.loads((HERE / 'lowered-fallback.json').read_text())['rows'] if r.get('direct_proposals'))
source = Path(root['source']).read_text()
assert hashlib.sha256(source.encode()).hexdigest() == root['source_sha256']
conn = sqlite3.connect(work / 'baseline-probe/trial.sqlite')
assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 0
error = 'ValueError: unsupported TU object identity; compiler_recipe.prepare selected tus.name=src/ui/level_preview.c instead of tus.object_path=build/src/ui/level_preview.o'
attempt = workspace.Attempt(False, 0, False, error, error, '')
workspace.record_attempt(conn, root['function'], source, attempt,
    strategy='narrow-transfer:viability:configuration-unavailable', run_id='narrow-transfer-20261002',
    done_reason='configuration-unavailable', extra={'training_eligible': False,
        'compiler_invocations': 0, 'stage': 'compiler-recipe-identity',
        'assistance': 'Assembly-only construction; no reference C body',
        'failure_logged_after_exception': True})
result = {'status': 'native_viability_configuration_unavailable', 'function': root['function'],
          'attempt_id': attempt.receipt_id, 'source_sha256': root['source_sha256'],
          'score_calls': 1, 'compiler_evaluations': 0, 'exact': False,
          'failure': error, 'comparison_run': False, 'training_eligible': False}
(HERE / 'baseline-probe.json').write_text(json.dumps(result, indent=2) + '\n')
conn.close()
print(json.dumps(result))

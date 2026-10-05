"""Read-only private receipt audit; emits no candidate source."""
import hashlib
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
path = Path('/home/grant/decomp/experiments/frontier-run-20260926/audio/attempts.sqlite')
conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
rows = conn.execute('select id,parent_attempt_id,compiled,score,exact,source_sha256,sampling from attempts order by id').fetchall()
edges = conn.execute('select count(*) from attempt_edges').fetchone()[0]
exact = [r for r in rows if r[4]]
assert len(rows) == 17 and edges == 16 and len(exact) == 1
assert exact[0][0] == 14 and exact[0][1] == 1
proof = json.loads(exact[0][6])['verification']
assert proof['exact'] and proof['status'] == 'object_sections_exact'
frontend = proof['frontend']
assert frontend['passed'] and frontend['status'] == 'passed'
root = Path('/mnt/c/Code/gameDecomp')
frozen = root / 'eval/results/resume-pipeline-20260908/code'
files = ('solver/workspace.py', 'solver/regalloc_mutations.py',
         'solver/byte_certificate.py', 'solver/frontend_check.py')
code = {}
for name in files:
    current = hashlib.sha256((root / name).read_bytes()).hexdigest()
    pinned = hashlib.sha256((frozen / name).read_bytes()).hexdigest()
    code[name] = {'current_sha256': current, 'frozen_sha256': pinned,
                  'same_as_frozen': current == pinned}
print(json.dumps({'attempt_rows': len(rows), 'lineage_edges': edges,
                  'exact_receipts': [r[0] for r in exact],
                  'candidate_source_sha256': exact[0][5],
                  'certificate_status': proof['status'],
                  'certificate_exact': proof['exact'],
                  'frontend_status': frontend['status'],
                  'frontend_passed': frontend['passed'],
                  'code': code}, indent=2))

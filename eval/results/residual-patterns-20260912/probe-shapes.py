import json
import sqlite3
import hashlib
from pathlib import Path
from solver import rewrites, code_shapes

root = Path('eval/results/residual-patterns-20260912')
records = json.loads((root/'current-v1/records.json').read_text())
names = ['drawCharacterSelectCoursePreviewPanel1', 'drawCharacterSelectCoursePreviewPanel3',
         'drawCharacterSelectCoursePreviewPanel5', 'startEndingSlashRepeatAnim']
out = []
with sqlite3.connect('file:/home/grant/decomp/partial-popup-v5-20260912/history.sqlite?mode=ro', uri=True) as conn:
    for record in records:
        if record['name'] not in names:
            continue
        source, score = conn.execute('SELECT source_code, score FROM attempts WHERE id=?', (record['attempt_id'],)).fetchone()
        assert hashlib.sha256(source.encode()).hexdigest() == record['source_sha256']
        (root/(record['name']+'.selected.c')).write_text(source)
        (root/(record['name']+'.selected.diff')).write_text(record['diff'])
        out.append({'name': record['name'], 'attempt_id': record['attempt_id'], 'score': score,
                    'size': record['size'], 'source': source,
                    'allocation_shaped': rewrites._allocation_shaped(record['diff']),
                    'ordering': len(rewrites.statement_order_rewrites(source, record['diff'])),
                    'shape_candidates': [v.label for v in code_shapes.candidates(source, record['name'])]})
print(json.dumps(out, indent=2))

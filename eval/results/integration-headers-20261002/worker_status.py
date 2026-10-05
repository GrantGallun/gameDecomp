"""Read only proposal/attempt progress counters, never prompts or generated source."""
from pathlib import Path
import json
import sqlite3
import time

rows = []
for slot in range(3):
    path = Path('/home/grant/decomp/campaign-workers-20260911') / str(slot) / 'worker.sqlite'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as conn:
        conn.execute('PRAGMA busy_timeout=1000')
        proposals = conn.execute('SELECT id,status,wall_ms,created_at FROM model_proposals ORDER BY id DESC LIMIT 3').fetchall()
        attempts = conn.execute('SELECT id,strategy,created_at FROM attempts ORDER BY id DESC LIMIT 2').fetchall()
    rows.append({'slot': slot, 'proposals': proposals, 'attempts': attempts})
result = {'at': time.time(), 'workers': rows}
(Path(__file__).resolve().parent / 'worker-status.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))

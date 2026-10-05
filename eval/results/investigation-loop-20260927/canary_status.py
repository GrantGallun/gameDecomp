import json
from pathlib import Path
import sqlite3
root = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
with sqlite3.connect((root / 'canary.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
    rows = conn.execute('SELECT id,run_id,status,kind,wall_ms,token_cost,length(raw_response),substr(raw_response,1,700) '
                        'FROM model_proposals ORDER BY id DESC LIMIT 6').fetchall()
    print(json.dumps({'recent_proposals': rows}, indent=2))
for proc in Path('/proc').glob('[0-9]*/cmdline'):
    try:
        command = proc.read_bytes().replace(b'\0', b' ').decode(errors='replace')
    except OSError:
        continue
    if any(term in command for term in ('run_canary.py', 'cc1', 'ido-static', 'score_repo_function.py')):
        print(proc.parent.name, command[:450])

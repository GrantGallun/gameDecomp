import json
from pathlib import Path
import sqlite3
import sys

root = Path(__file__).resolve().parent
trial = sys.argv[1] if len(sys.argv) > 1 else 'popup-v2'
state = json.loads((root / trial / 'state.json').read_bytes())
with sqlite3.connect(f'file:{state["db"]}?mode=ro', uri=True) as conn:
    rows = []
    for call in state['calls']:
        raw, sampling = conn.execute('SELECT raw_response,sampling FROM model_proposals WHERE id=?',
                                    (call['proposal_id'],)).fetchone()
        rows.append({**call, 'raw_response': raw, 'sampling': json.loads(sampling)})
result = {'status': state['status'], 'current': state['current'], 'calls': rows}
(root / ('model-trial.json' if trial == 'popup-v2' else f'{trial}-model-trial.json')).write_text(json.dumps(result, indent=2))
for row in rows:
    print(json.dumps({k: v for k, v in row.items() if k not in ('sampling', 'raw_response')}))

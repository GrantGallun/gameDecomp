import json
import sqlite3
from pathlib import Path

db = Path('/home/grant/decomp/partial-popup-v3-20260912/history.sqlite')
with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
    print('columns', [r[1] for r in conn.execute('pragma table_info(attempts)')])
    print('functions', [r[1] for r in conn.execute('pragma table_info(functions)')])
    print('state', json.loads(Path(__file__).with_name('routing-state.json').read_text())['nodes']['calculateFixedAngleFromDeltaXZ'].keys())

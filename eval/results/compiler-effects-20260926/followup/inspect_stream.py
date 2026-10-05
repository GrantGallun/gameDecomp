"""Read-only bounded proposal-order inspection of the measured MusAsk path."""
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))
from solver import regalloc_mutations

RUN = Path('/home/grant/decomp/experiments/compiler-effects-20260926')
base = json.loads((RUN / 'private/MusAsk/result.json').read_text())
confirmed = json.loads((HERE / 'confirmation.json').read_text())
rows = []
with sqlite3.connect((RUN / 'private/MusAsk/attempts.sqlite').as_uri() + '?mode=ro', uri=True) as db:
    for name, receipt, wanted in [('baseline', base['baseline']['receipt_id'], 'stmt_move:6->7'),
                                  ('after-order', confirmed['actual_parent_receipt_id'], 'local_type:u16->s32@379')]:
        source, diff, raw = db.execute('SELECT source_code,diff_summary,sampling FROM attempts WHERE id=?', (receipt,)).fetchone()
        sampling = json.loads(raw)
        evidence = {key: sampling.get(key) for key in ('source_attribution', 'frontend', 'compiler_recipe')}
        labels = []
        exhausted = True
        for label, family, _ in regalloc_mutations.variants(source, 'MusAsk', diff=diff, evidence=evidence):
            labels.append({'ordinal': len(labels) + 1, 'label': label, 'family': family})
            if len(labels) >= 301:
                exhausted = False
                break
        rows.append({'parent': name, 'receipt_id': receipt, 'exhausted': exhausted,
                     'count_or_lower_bound': len(labels), 'wanted': wanted,
                     'wanted_ordinal': next((x['ordinal'] for x in labels if x['label'] == wanted), None),
                     'labels': labels})
(HERE / 'MusAsk-stream.json').write_text(json.dumps(rows, indent=2))
print(json.dumps([{k: v for k, v in row.items() if k != 'labels'} for row in rows], indent=2))

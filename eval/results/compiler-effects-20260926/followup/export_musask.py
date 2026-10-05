"""Export the measured candidate/assembly pair for human review, never reference C."""
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3

HERE = Path(__file__).resolve().parent
RUN = Path('/home/grant/decomp/experiments/compiler-effects-20260926')
confirmation = json.loads((HERE / 'confirmation.json').read_text())
assert confirmation['confirmed'] and confirmation['function'] == 'MusAsk'
folder = HERE / 'MusAsk'
folder.mkdir(exist_ok=False)
with sqlite3.connect(Path(confirmation['confirmation_private_db']).as_uri() + '?mode=ro', uri=True) as db:
    parent = db.execute('SELECT source_code,parent_attempt_id FROM attempts WHERE id=?',
                        (confirmation['actual_parent_receipt_id'],)).fetchone()
    original = db.execute('SELECT source_code FROM attempts WHERE id=?', (parent[1],)).fetchone()[0]
    source, sampling = db.execute('SELECT source_code,sampling FROM attempts WHERE id=?',
                                 (confirmation['confirmation_receipt_id'],)).fetchone()
assert hashlib.sha256(source.encode()).hexdigest() == confirmation['source_sha256']
for name, text in [('baseline.c', original), ('after-order-edit.c', parent[0]), ('confirmed.c', source)]:
    (folder / name).write_text(text)
ws = RUN / 'private/MusAsk/repo/nonmatchings/MusAsk'
(folder / 'confirmed.s').write_bytes((ws / 'MusAsk_ce_followup_independent_confirmation_object_dump_normalized.s').read_bytes())
(folder / 'target.s').write_bytes((ws / 'target_object_dump_normalized.s').read_bytes())
(folder / 'edit.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), source.splitlines(True),
                                                           fromfile='retained', tofile='confirmed')))
observations = json.loads(sampling)
(folder / 'certificate.json').write_text(json.dumps({key: observations.get(key)
                                                    for key in ('verification', 'frontend', 'compiler_recipe')}, indent=2))
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
(folder / 'manifest.json').write_text(json.dumps({'regime': 'header-assisted; known research match reproduced',
                                                'training_eligible': False, 'files': hashes}, indent=2))
print((folder / 'edit.diff').read_text())

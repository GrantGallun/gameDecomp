"""Bounded isolated controller benchmark; the live databases are read-only."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
import zlib

from eval import campaign_workers, frozen_wavefront

out = Path(__file__).resolve().parent
live = out.parent/'resume-pipeline-20260908'
native = Path('/home/grant/decomp/optimization-validation-20260911/controller')
native.mkdir(parents=True,exist_ok=True)
main = out/'controller-runs.sqlite'
if main.exists():
    raise SystemExit('Benchmark output exists; retain it and choose a new output path')
with sqlite3.connect(f'file:{live / "campaign.sqlite"}?mode=ro',uri=True) as src:
    rows = src.execute('SELECT * FROM attempt_runs').fetchall()
with sqlite3.connect(main) as dst:
    dst.executescript(Path('kb/schema.sql').read_text())
    dst.executemany('INSERT INTO attempt_runs VALUES (?,?,?,?,?)', rows)
base = native/'baseline.sqlite'
prior = campaign_workers.synchronize(main,base)
with sqlite3.connect(main) as dst:
    dst.executemany('INSERT INTO attempt_runs VALUES (?,?,?,?,?)',
                    [(f'benchmark-new-{i}','unattached','','{"payload":"'+('x'*5000)+'"}',0) for i in range(10)])

def old_sync(private):
    with sqlite3.connect(private,timeout=120,uri=True) as dst:
        dst.execute('ATTACH DATABASE ? AS upstream',(f'file:{main}?mode=ro',))
        dst.execute('DELETE FROM attempt_edges WHERE child_attempt_id>?',(prior['attempts'],))
        dst.execute('DELETE FROM model_proposals WHERE id>?',(prior['model_proposals'],))
        dst.execute('DELETE FROM attempts WHERE id>?',(prior['attempts'],))
        dst.execute('DELETE FROM attempt_runs WHERE id NOT IN (SELECT id FROM upstream.attempt_runs)')
        dst.execute('INSERT OR IGNORE INTO attempt_runs SELECT * FROM upstream.attempt_runs')
        dst.execute('INSERT INTO attempts SELECT * FROM upstream.attempts WHERE id>?',(prior['attempts'],))
        dst.execute('INSERT INTO model_proposals SELECT * FROM upstream.model_proposals WHERE id>?',(prior['model_proposals'],))
        dst.execute('INSERT INTO attempt_edges SELECT * FROM upstream.attempt_edges WHERE child_attempt_id>?',(prior['attempts'],))
        return campaign_workers.maxima(dst)

samples=[]
for trial in range(3):
    for arm in (('baseline','optimized') if trial%2==0 else ('optimized','baseline')):
        private = native/f'{arm}-{trial}.sqlite'
        shutil.copy2(base,private)
        started=time.perf_counter()
        result=old_sync(private) if arm=='baseline' else campaign_workers.synchronize(main,private,prior)
        elapsed=time.perf_counter()-started
        with sqlite3.connect(main) as src, sqlite3.connect(private) as dst:
            assert src.execute('SELECT * FROM attempt_runs ORDER BY id').fetchall()==dst.execute('SELECT * FROM attempt_runs ORDER BY id').fetchall()
            assert not dst.execute('PRAGMA foreign_key_check').fetchall()
        samples.append({'trial':trial,'arm':arm,'seconds':elapsed,'maxima':result})

pointer=json.loads((live/'campaign.json').read_bytes())
with sqlite3.connect(f'file:{live/pointer["store"]}?mode=ro',uri=True) as src:
    manifest=json.loads(src.execute('SELECT manifest FROM commits WHERE id=?',(pointer['commit'],)).fetchone()[0])
    state=json.loads(zlib.decompress(src.execute('SELECT payload FROM objects WHERE hash=?',(manifest['metadata'],)).fetchone()[0]))
pins=state['pins']
def original_verify():
    changed=[name for name,digest in pins.items() if not Path(name).is_file() or hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest]
    assert not changed
hash_samples=[]
for trial in range(2):
    for arm in (('baseline','optimized') if trial==0 else ('optimized','baseline')):
        started=time.perf_counter()
        original_verify() if arm=='baseline' else frozen_wavefront.verify_files(pins)
        hash_samples.append({'trial':trial,'arm':arm,'seconds':time.perf_counter()-started})
report={'scope':'isolated run-metadata-only DB; no candidate/model throughput claim',
        'run_rows':len(rows),'run_payload_bytes':sum(len(v) if isinstance(v,(str,bytes)) else 8 for r in rows for v in r if v is not None),
        'sync':samples,'all_run_rows_and_configs_identical':True,'pin_count':len(pins),
        'hash':hash_samples,'all_pin_bytes_verified':True}
(out/'controller-benchmark.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))

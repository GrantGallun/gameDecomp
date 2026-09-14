"""Bind final preparer code to the exact source manifest already whole-ROM verified."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import prepare_integration
BASE=Path('/mnt/c/Code/gameDecomp/eval/results/small-functions-20260913/linker-union-1789318026368143892/sentinel-1789318340254484789')
OUT=BASE/('final-prepare-check-'+str(time.time_ns()))
OUT.mkdir()
manifest=BASE/'prepared/manifest.json'
spec=json.loads(manifest.read_bytes())
entries=[]
with closing(sqlite3.connect((BASE/'source-bindings.sqlite').as_uri()+'?mode=ro',uri=True)) as conn:
    for item in spec['lineage']:
        source=conn.execute('SELECT source_code FROM attempts WHERE id=?',(item['attempt_id'],)).fetchone()[0]
        path=OUT/(item['function']+'.c')
        path.write_text(source)
        entries.append({'function':item['function'],'source':str(path),'attempt_id':item['attempt_id'],'verification':item['verification']})
fresh=prepare_integration.prepare(repo=Path('/home/grant/decomp/sbk1'),db=BASE/'source-bindings.sqlite',entries=entries,output_dir=OUT/'prepared')
identical=fresh.read_bytes()==manifest.read_bytes()
assert identical, 'Final preparer changed proven integration manifest'
report={'manifest_identical':identical,'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'final_preparer_sha256':hashlib.sha256(Path(prepare_integration.__file__).read_bytes()).hexdigest(),
        'whole_rom_receipt':str(BASE/'seven-integration.json'),'live_mutated':False}
(OUT/'report.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))

"""Recover the already compiled first attempt after its harness attribute error."""
import hashlib, json, sqlite3, sys
from pathlib import Path
here=Path(__file__).resolve().parent
sys.path.insert(0,str(here.parents[2]))
from solver import workspace

folder=here/'portable/initial-failure'
conn=sqlite3.connect(folder/'attempts.sqlite')
assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0
work=folder/'compiles/drawMenuSprite/baseline/attempt-00001'
receipt=json.loads((work/'receipt.json').read_text())
source=(work/'source.c').read_text()
assert hashlib.sha256(source.encode()).hexdigest()==receipt['source_sha256']
identity=json.loads((folder/'environment.json').read_text())
att=workspace.Attempt(receipt['compiled'],0,receipt['exact'],(work/'candidate_diff').read_text(),
    receipt.get('error') or '', '', verification=receipt.get('verification'),frontend=receipt.get('frontend'),
    compiler_recipe=identity['recipes'][receipt['compile_target']])
attempt_id=workspace.record_attempt(conn,'drawMenuSprite',source,att,strategy='m2c-lifting-dev-spike:baseline',
    run_id=folder.name,run_kind='dev-spike',wall_ms=int(receipt['seconds']*1000),
    extra={'training_eligible':False,'score_available':False,
        'recording_recovery':'Original harness accessed Compiled.asm instead of Compiled.dump after receipt write; recovered from unchanged retained source and receipt.'})
conn.close()
(folder/'recovery.json').write_text(json.dumps({'attempt_id':attempt_id,'new_compiles':0,
    'receipt_sha256':hashlib.sha256((work/'receipt.json').read_bytes()).hexdigest(),
    'source_sha256':receipt['source_sha256'],'compiled':receipt['compiled'],'exact':receipt['exact']},indent=2)+'\n')
print(json.dumps({'attempt_recovered':attempt_id,'new_compiles':0}))

"""Retain bounded portable artifacts, leaving private compiler DB native."""
import hashlib
import json
from pathlib import Path
import shutil

OUT=Path(__file__).resolve().parent
native=Path('/home/grant/decomp/pointer-loop-20260912/drawCharacterSelectCoursePreviewFrame-1789251729539088696')
destination=OUT/'round2-results'
destination.mkdir(exist_ok=False)
names=['summary.json','panel-report.json']
for index in (0,6,9,22):
    names += [f'attempt-{index:03d}.{suffix}' for suffix in ('source.c','diff.txt','compiler.txt','json')]
hashes={}
for name in names:
    raw=(native/name).read_bytes()
    (destination/name).write_bytes(raw)
    hashes[name]=hashlib.sha256(raw).hexdigest()
(destination/'artifact-hashes.json').write_text(json.dumps({'native_workspace':str(native),'files':hashes},indent=2))
shutil.copyfile(destination/'attempt-006.source.c',OUT/'best-candidate.c')
shutil.copyfile(destination/'attempt-006.diff.txt',OUT/'best-candidate.diff.txt')
summary=json.loads((destination/'summary.json').read_bytes())
best=summary['attempts'][6]
assert best['score']==93.379 and best['semantic']['status']=='observed_pass'
targeted=json.loads((OUT/'selector-round2/attempt-006.json').read_bytes())
assert targeted['counts']=={'passed':3} and targeted['source_sha256']==best['source_sha256']
assert hashlib.sha256((OUT/'best-candidate.c').read_bytes()).hexdigest()==best['source_sha256']
(OUT/'best-candidate.json').write_text(json.dumps({'source_sha256':best['source_sha256'],
    'selected_original_attempt':summary['selected_attempt_id'],
    'private_attempt_id':best['attempt_id'],'native_workspace':str(native),
    'score':best['score'],'exact':best['exact'],'frontend_passed':best['gates']['frontend_passed'],
    'standard_semantic':best['semantic']['status'],'targeted_counts':targeted['counts'],
    'targeted_panel_sha256':targeted.get('panel_sha256'),
    'promotion':False,'live_import':False},indent=2))
print(json.dumps({'best':best['score'],'totals':summary['totals'],'output':str(destination)}))

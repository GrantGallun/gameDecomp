import hashlib
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
attempt=json.loads((OUT/'round2-results/attempt-006.json').read_bytes())
checked=[]
for name,item in attempt['artifacts'].items():
    assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'],name
    checked.append(name)
source=(OUT/'best-candidate.c').read_bytes()
assert hashlib.sha256(source).hexdigest()==attempt['source_sha256']
result=json.loads((OUT/'selector-round2/attempt-006.json').read_bytes())
assert result['source_sha256']==attempt['source_sha256'] and result['normal_panel_identity_verified']
(OUT/'best-binding-verification.json').write_text(json.dumps({
    'source_sha256':attempt['source_sha256'],'checked_artifacts':checked,
    'targeted_case_sha256':result['case_sha256'],'panel_sha256':result['panel_sha256'],
    'counts':result['counts'],'all_passed':True},indent=2))
print(json.dumps({'checked_artifacts':len(checked),'all_passed':True}))

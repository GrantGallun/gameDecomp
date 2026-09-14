import hashlib,json,re
from pathlib import Path
out=Path(__file__).resolve().parent; d=json.loads((out/'inventory.json').read_text())
names=['Fcutoff','Fdrums','Fendit','initRaceCameraChase','initRaceCameraCourseStart','initRaceCameraFixedPositionFollow','initRaceCameraPositionTransition','initRaceCameraRotationTransition','initAudioDmaCallback']
refs=[]
for key,expected in d['pins'].items():
 if '/asm/data/' not in key or not key.endswith('.s'): continue
 b=Path(key).read_bytes()
 if hashlib.sha256(b).hexdigest()!=expected: continue
 text=b.decode(errors='replace'); lines=text.splitlines()
 for i,line in enumerate(lines):
  hits=[n for n in names if re.search(r'\b'+re.escape(n)+r'\b',line)]
  if hits: refs.append({'path':key,'sha256':expected,'line':i+1,'functions':hits,'context':lines[max(0,i-3):i+4]})
(out/'binary-data-callback-refs.json').write_text(json.dumps({'checkpoint':d['checkpoint'],'references':refs},indent=2))
print(json.dumps(refs,indent=2))

import json,re,collections
from pathlib import Path
from solver import binary_data as b
out=Path(__file__).resolve().parent; d=json.loads((out/'inventory.json').read_text()); samples={}; counts=collections.Counter()
for p in d['pins']:
 if '/asm/data/' not in p or not p.endswith('.s'):continue
 for line in Path(p).read_text().splitlines():
  clean=re.sub(r'/\*.*?\*/','',line).strip()
  if clean.startswith('.') and not b.ANNOTATED.match(line) and not any(clean.startswith(x) for x in ['.section','.include','.space','.align','.balign']):
   name=clean.split()[0];counts[name]+=1;samples.setdefault(name,line[:300])
print(json.dumps({'counts':counts,'samples':samples},indent=2))

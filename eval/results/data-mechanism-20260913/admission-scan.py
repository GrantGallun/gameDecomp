"""Read-only real ELF ownership/admission audit for every selected memory context."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from solver import data_memory,workspace

ROOT=Path('/mnt/c/Code/gameDecomp')
OUT=ROOT/'eval/results/data-mechanism-20260913'
path=next((OUT/'pilot/binary-data').glob('*.json'))
raw=path.read_bytes()
artifact=json.loads(raw)
repo=Path('/home/grant/decomp/sbk1')
rows=[]
for function,context in artifact['memory'].items():
    ws=repo/'nonmatchings'/function
    paths={name:ws/name for name in ('target.s','target.o','target_object_dump_normalized.s')}
    row={'function':function,'selected_regions':len(context['regions']),'selected_bytes':sum(len(bytes.fromhex(r['bytes_hex'])) for r in context['regions'])}
    try:
        hashes={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in paths.items()}
        row['inputs']={name:{'path':str(p),'sha256':hashes[name]} for name,p in paths.items()}
        raw_target=paths['target.s'].read_text()
        row['target_assembly_bound']=hashlib.sha256(raw_target.encode()).hexdigest()==context['target_assembly_sha256']
        assembly=workspace.semantic_assembly(paths['target_object_dump_normalized.s'].read_text(),paths['target.o'])
        mapped,admission=data_memory.apply(assembly,context,raw_target=raw_target)
        row['admission']=admission
        row['status']=admission['status']
        row['mapped_assembly_sha256']=hashlib.sha256(mapped.encode()).hexdigest()
    except (OSError,ValueError) as exc:
        row['status']='unavailable'
        row['error']=type(exc).__name__+': '+str(exc)
    rows.append(row)
summary={'contexts':len(rows),'statuses':dict(Counter(r['status'] for r in rows)),
         'admitted_regions':sum(len((r.get('admission') or {}).get('regions',[])) for r in rows),
         'mapped_bytes_per_function_sum':sum((r.get('admission') or {}).get('mapped_bytes',0) for r in rows),
         'decline_reasons':dict(Counter(d['reason'] for r in rows for d in (r.get('admission') or {}).get('declines',[]))),
         'bound_raw_target':sum(r.get('target_assembly_bound') is True for r in rows)}
result={'context_artifact':str(path),'context_artifact_sha256':hashlib.sha256(raw).hexdigest(),
        'modules':{str(Path(m.__file__)):hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (data_memory,workspace)},
        'summary':summary,'rows':rows,'live_mutated':False}
(OUT/'admission-scan.json').write_text(json.dumps(result,indent=2))
print(json.dumps(summary,indent=2))
print('admitted:',[(r['function'],r['admission']['mapped_bytes'],[a['symbol'] for a in r['admission']['regions']]) for r in rows if r['status']=='admitted'])

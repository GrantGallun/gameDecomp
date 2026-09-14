"""Read-only distribution audit for treemap interpretation."""
from eval.campaign_state import read
from eval.progress_map import project
from pathlib import Path
from collections import Counter
import json

run=Path('eval/results/resume-pipeline-20260908')
s=read(run/'campaign.json')
rows=[project(n,v,s.get('tu_index',{}).get(n,'?'))[0] for n,v in s['nodes'].items()]
total=sum(r['size'] or 0 for r in rows)
def stats(rr):
    return {'functions':len(rr),'bytes':sum(r['size'] or 0 for r in rr),
            'exact_functions':sum(r['category']=='exact' for r in rr),
            'exact_bytes':sum(r['size'] or 0 for r in rr if r['category']=='exact'),
            'work_items':sum(r['work_items'] for r in rr)}
result={'by_status':{c:stats([r for r in rows if r['category']==c]) for c in sorted({r['category'] for r in rows})},
 'by_size':{f'{lo}-{hi}':stats([r for r in rows if lo<=(r['size'] or 0)<hi]) for lo,hi in [(0,64),(64,256),(256,1024),(1024,10000000)]},
 'largest_unfinished':[{k:r[k] for k in ('name','size','category','score','work_items')} for r in sorted(rows,key=lambda r:-(r['size'] or 0)) if r['category']!='exact'][:12],
 'largest_unfinished_share':{n:100*sum(r['size'] or 0 for r in sorted([r for r in rows if r['category']!='exact'],key=lambda r:-(r['size'] or 0))[:n])/total for n in [10,25,50,100]},
 'compile_errors':Counter((s['nodes'][r['name']].get('residual') or {}).get('compiler_error_signature','') for r in rows if r['category']=='compile_blocked').most_common(8),
 'semantics':dict(Counter((v.get('semantic_validation') or {}).get('status','missing') for v in s['nodes'].values())),
 'near':{score:stats([r for r in rows if r['category']=='partial' and (r['score'] or 0)>=score]) for score in [90,95,99]}}
Path(__file__).with_suffix('.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))

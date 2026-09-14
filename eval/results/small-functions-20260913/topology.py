from collections import Counter
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
data = json.loads((OUT/'inventory.json').read_bytes())
rows = [r for r in data['rows'] if r['status'] not in {'object_exact','integrated'}]
weak = [r for r in rows if r['cluster_functions'] <= 3]
def compact(r):
    return {k:r.get(k) for k in ('function','size','status','score','cluster','cluster_functions','callers','callees')} | {
        'compiled':(r.get('residual') or {}).get('compiled'),
        'semantic':(r.get('semantic_validation') or {}).get('status'),
        'blocker':(r.get('blocker') or {}).get('status')}
(OUT/'compact.json').write_text(json.dumps([compact(r) for r in weak],indent=2))
summary = {'checkpoint':data['checkpoint'], 'weak_cluster_unresolved':len(weak),
           'tiny_weak':sum(r['size']<=128 for r in weak),
           'weak_with_callers':sum(bool(r['callers']) for r in weak),
           'weak_no_known_edges':sum(not r['callers'] and not r['callees'] for r in weak),
           'small_no_known_edges':len([r for r in rows if not r['callers'] and not r['callees']]),
           'status':dict(Counter(r['status'] for r in weak)),
           'blockers':dict(Counter((r.get('blocker') or {}).get('status','none') for r in weak)),
           'semantics':dict(Counter((r.get('semantic_validation') or {}).get('status','none') for r in weak)),
           'no_known_edges':[compact(r) for r in rows if not r['callers'] and not r['callees']]}
(OUT/'topology.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k!='no_known_edges'}))

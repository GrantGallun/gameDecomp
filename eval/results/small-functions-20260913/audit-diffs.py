"""Read selected current attempts only, bound to immutable inventory13212."""
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from solver import signals

OUT=Path(__file__).resolve().parent
raw=(OUT/'inventory.json').read_bytes()
inventory=json.loads(raw)
small=[r for r in inventory['rows'] if r.get('size',999999)<=256 and r['status'] not in {'object_exact','integrated'}]
selected=[r for r in small if r['cluster_functions']<=3 or not r['callers'] and not r['callees']]
rows=[]
with closing(sqlite3.connect(Path(inventory['db']).as_uri()+'?mode=ro',uri=True,timeout=30)) as conn:
    conn.row_factory=sqlite3.Row
    for node in selected:
        r={k:node.get(k) for k in ('function','size','status','attempt_id','source_sha256','score','cluster','cluster_functions','callers','callees','blocker')}
        r['small_cluster']=node['cluster_functions']<=3
        r['no_known_call_edges']=not node['callers'] and not node['callees']
        semantic=node.get('semantic_validation') or {}
        r['semantic']={k:semantic.get(k) for k in ('status','counts','reason','feedback','source_sha256','unsupported_callees','execution_debt')}
        r['residual']=node.get('residual')
        verification=node.get('verification') or {}
        r['verification']={k:v for k,v in verification.items() if k not in {'build_inputs','compiler_recipe','frontend'}}
        r['frontend']=(node.get('residual') or {}).get('frontend')
        r['last_outcome']=node.get('last_outcome')
        if node.get('attempt_id'):
            a=conn.execute('SELECT id,source_code,source_sha256,compiled,compiler_stderr,score,exact,diff_summary,strategy FROM attempts WHERE id=?',(node['attempt_id'],)).fetchone()
            if a is None: raise ValueError('missing attempt')
            source_sha=hashlib.sha256(a['source_code'].encode()).hexdigest()
            if source_sha!=node['source_sha256']: raise ValueError('source identity mismatch: '+node['function'])
            r['attempt']=dict(a)
            r['signals']=asdict(signals.analyse(a['diff_summary'] or '',a['score'] or 0,bool(a['exact']),bool(a['compiled'])))
            changed=[l[1:] for l in (a['diff_summary'] or '').splitlines() if l[:1] in {'+','-'} and not l.startswith(('+++','---'))]
            r['changed_opcode_counts']=dict(Counter(m.group(1) for l in changed if (m:=re.match(r'\s*([a-z][a-z0-9.]*)\b',l))))
            r['changed_stack_lines']=[l for l in changed if re.search(r'\bsp\b|\$29\b',l)]
        rows.append(r)
summary={}
for label,group in [('small_cluster',[r for r in rows if r['small_cluster']]),('no_known_call_edges',[r for r in rows if r['no_known_call_edges']])]:
    summary[label]={'count':len(group),'tiny':sum(r['size']<=128 for r in group),
        'statuses':dict(Counter(r['status'] for r in group)),
        'semantic_statuses':dict(Counter(r['semantic']['status'] for r in group)),
        'no_selected_attempt':sum('attempt' not in r for r in group),
        'compile_failed':sum(not r['attempt']['compiled'] for r in group if 'attempt' in r),
        'normalized_assembly_exact':sum(r['verification'].get('normalized_assembly_exact') is True for r in group),
        'stack_diff_functions':sum(bool(r.get('changed_stack_lines')) for r in group),
        'signal_functions':{k:sum(bool(r.get('signals',{}).get(k)) for r in group) for k in ('structural','offset','width','reloc','regalloc','ordering','immediate')},
        'changed_opcodes':dict(sum((Counter(r.get('changed_opcode_counts',{})) for r in group),Counter()).most_common(20))}
result={'checkpoint':inventory['checkpoint'],'inventory_sha256':hashlib.sha256(raw).hexdigest(),
        'scope':'Selected current C sources and full saved assembly diffs; no reference C read; no builds or campaign mutations',
        'summary':summary,'rows':rows}
(OUT/'diff-audit.json').write_text(json.dumps(result,indent=2))
print(json.dumps(summary,indent=2))

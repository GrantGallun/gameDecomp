import collections
from contextlib import closing
import difflib
import json
from pathlib import Path
import sqlite3

OUT=Path(__file__).resolve().parent
report=json.loads((OUT/'audit.json').read_bytes())
rows=report['rows']
details=[]
proposals=[]
with closing(sqlite3.connect(Path(report['config_db']).as_uri()+'?mode=ro',uri=True,timeout=30)) as conn:
    conn.row_factory=sqlite3.Row
    for item in [r for r in rows if not r['best_score_improved'] and r['source_changed']]:
        candidate=conn.execute('SELECT id,parent_attempt_id,func_addr,source_code,source_sha256,score,strategy FROM attempts WHERE id=?',(item['attempt_id'],)).fetchone()
        # Indexed function lookup, source match; never scans the entire attempt DB.
        before=conn.execute('SELECT id,source_code,source_sha256,score,strategy FROM attempts WHERE func_addr=? AND source_sha256=? ORDER BY iteration DESC LIMIT 1',
                            (candidate['func_addr'],item['source_sha256'])).fetchone()
        without_includes=lambda s: '\n'.join(line for line in s.splitlines() if not line.lstrip().startswith('#include'))
        details.append({'function':item['function'],'profile':item['profile'],'before_id':before['id'] if before else None,
                        'after_id':candidate['id'],'before_score':before['score'] if before else None,
                        'after_score':candidate['score'],'after_strategy':candidate['strategy'],
                        'includes_only':bool(before and without_includes(before['source_code']) == without_includes(candidate['source_code'])),
                        'diff':''.join(difflib.unified_diff(before['source_code'].splitlines(True),candidate['source_code'].splitlines(True),fromfile='before',tofile='after')) if before else None})
    ids=sorted({int(v) for r in rows for v in ((r['private_lineage'] or {}).get('proposal_ids') or {}).values()})
    for ident in ids:
        p=conn.execute('SELECT id,parent_attempt_id,child_attempt_id,status,kind,wall_ms,token_cost,sampling,raw_response FROM model_proposals WHERE id=?',(ident,)).fetchone()
        if p:
            data=dict(p)
            data['raw_response_excerpt']=data.pop('raw_response')[:1400]
            proposals.append(data)
(OUT/'source-diffs.json').write_text(json.dumps(details,indent=2))
(OUT/'proposals.json').write_text(json.dumps(proposals,indent=2))
print(json.dumps({'sources':details[:5], 'changed_zero_gain_count':len(details),
                  'includes_only_count':sum(d['includes_only'] for d in details),
                  'includes_only_by_profile':dict(collections.Counter(d['profile'] for d in details if d['includes_only'])),
                  'proposal_statuses':dict(collections.Counter(p['status'] for p in proposals)),
                  'proposal_count':len(proposals)},indent=2))

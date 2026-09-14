import json,sqlite3
from pathlib import Path
p=Path(__file__).resolve().parent.parent/'resume-pipeline-20260908/campaign.sqlite'
with sqlite3.connect(f'file:{p}?mode=ro',uri=True,timeout=20) as c:
    c.row_factory=sqlite3.Row
    print([r[0] for r in c.execute("select name from sqlite_master where type='table' and name like '%proposal%'")])
    for r in c.execute('select * from model_proposals order by id desc limit 3'):
        print(json.dumps({k:r[k] for k in r.keys() if k in ('id','status','raw_response','sampling_json','run_id')}))
    rows=list(c.execute('select id,status,raw_response from model_proposals order by id desc limit 50'))
    print(json.dumps({'last_50':len(rows),'context_rejected':sum('num_ctx headroom exceeded' in str(r['raw_response']) for r in rows),
                      'statuses':{s:sum(r['status']==s for r in rows) for s in {r['status'] for r in rows}}}))

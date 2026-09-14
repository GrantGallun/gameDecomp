import json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval import campaign_state
run=ROOT/'eval/results/resume-pipeline-20260908'
state=campaign_state.read(run/'campaign.json')
for name,node in state['nodes'].items():
    if node.get('status')=='pending' and (node.get('residual') or {}).get('compiled'):
        print(name,json.dumps({k:v for k,v in node.items() if k not in ('semantic_validation','jobs','residual','frontier')}))
        print('residual',json.dumps(node.get('residual'))[:1800])
        with sqlite3.connect(f'file:{run/"campaign.sqlite"}?mode=ro',uri=True) as c:
            print('columns',[r[1] for r in c.execute('pragma table_info(attempts)')])
            c.row_factory=sqlite3.Row
            row=c.execute('select * from attempts where id=?',(node['attempt_id'],)).fetchone()
            print({k:str(row[k])[:1800] for k in row.keys() if k in ('function','function_name','diff','source_sha256','compiled','exact')})
        break

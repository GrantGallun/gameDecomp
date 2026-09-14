import json
import sqlite3
from pathlib import Path

out = Path(__file__).parent
db = out.parent/'resume-pipeline-20260908/campaign.sqlite'
with sqlite3.connect(f'file:{db}?mode=ro',uri=True,timeout=20) as conn:
    conn.row_factory=sqlite3.Row
    rows = [dict(r) for r in conn.execute('SELECT id,prompt_context,sampling,raw_response FROM model_proposals ORDER BY id DESC LIMIT 50')]
for row in rows:
    prompt=row['prompt_context']
    (out/('prompt-'+str(row['id'])+'.json')).write_text(json.dumps(row,indent=2))
    start='FAILING OBSERVABLES AND EXECUTED VALUE TREES:\n'
    if start in prompt:
        packet=json.loads(prompt.split(start,1)[1].split('\nCURRENT C:\n',1)[0])
        fields={k:len(json.dumps(v)) for k,v in packet.items()}
        primary=packet.get('primary_counterexample') or {}
        print(json.dumps({'id':row['id'],'total':len(prompt),'fields':fields,
                          'primary_fields':{k:len(json.dumps(v)) for k,v in primary.items()}}))
    else:
        print(json.dumps({'id':row['id'],'total':len(prompt),'semantic':False}))

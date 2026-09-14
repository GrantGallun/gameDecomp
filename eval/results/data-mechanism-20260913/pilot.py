"""Read pinned checkpoint evidence; build private catalog without live mutation."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from eval import campaign_state, campaign_data

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
pointer=json.loads((RUN/'campaign.json').read_bytes())
with closing(sqlite3.connect((RUN/pointer['store']).as_uri()+'?mode=ro',uri=True)) as conn:
    state=campaign_state._hydrate(conn,pointer)
state['fast_inflight']=[]
state['inflight']=None
started=time.monotonic()
bundle,changed=campaign_data.prepare(state,Path(state['config']['repo']),OUT/'pilot')
report={'checkpoint':pointer['commit'],'seconds':time.monotonic()-started,
        'record':state['binary_data_catalog'],'changed_nodes':len(changed),
        'readonly_examples':list(bundle['memory'])[:20]}
campaign_state.atomic(OUT/'catalog-pilot.json',report)
print(json.dumps(report))

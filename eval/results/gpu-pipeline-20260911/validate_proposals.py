"""Replay benchmark edits against their recorded parents in private workspaces."""
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval.campaign_workers import isolate
from solver import modelrepair, workspace

def main():
    output=Path(__file__).parent
    results=[]
    with sqlite3.connect('file:'+str(ROOT/'eval/results/resume-pipeline-20260908/campaign.sqlite')+'?mode=ro',uri=True) as conn:
        for label in ('f16-one','q8-one','q8-two'):
            for row in json.loads((output/(label+'.json')).read_bytes())['results']:
                source,function=conn.execute('SELECT a.source_code,f.name FROM model_proposals p JOIN attempts a ON a.id=p.parent_attempt_id JOIN functions f ON f.addr=a.func_addr WHERE p.id=?',(row['proposal_id'],)).fetchone()
                result={'label':label,'proposal_id':row['proposal_id'],'function':function}
                try:
                    proposal=modelrepair.parse_proposal(row['response']['message']['content'],source=source)
                    candidate=modelrepair.apply_proposal(source,proposal)
                    repo=isolate(Path('/home/grant/decomp/sbk1'),Path('/home/grant/decomp/gpu-validation-20260911')/label/function,function)
                    ws=workspace.bootstrap(repo,function)
                    attempt=workspace.score(ws,repo,function,candidate)
                    result.update(parsed=True,applied=True,compiled=attempt.compiled,score=attempt.score,exact=attempt.exact,frontend=attempt.frontend)
                    (output/(label+'-'+str(row['proposal_id'])+'.c')).write_text(candidate)
                except ValueError as exc:result.update(accepted=False,error=str(exc))
                results.append(result)
    (output/'proposal-validation.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()

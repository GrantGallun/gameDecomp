"""Paired saved-prompt replay; separate output from the authoritative campaign."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import llm,modelrepair

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('adaptive','fixed'),required=True)
    args=p.parse_args()
    service=json.loads((ROOT/'eval/results/resume-pipeline-20260908/service.json').read_bytes())
    if service['status']!='paused' or service.get('worker_pid'):raise ValueError('campaign must be paused')
    endpoint='http://127.0.0.1:11435'
    with sqlite3.connect('file:'+str(ROOT/'eval/results/resume-pipeline-20260908/campaign.sqlite')+'?mode=ro',uri=True) as c:
        rows={row[0]:row for row in c.execute('SELECT p.id,p.prompt_context,a.source_code,f.name FROM model_proposals p JOIN attempts a ON a.id=p.parent_attempt_id JOIN functions f ON f.addr=a.func_addr WHERE p.id IN (2353,2382)')}
    # Both arms begin with an already-loaded 32k model. Warmup is retained but
    # excluded from request timing. There are no cached *responses* in either arm.
    req=urllib.request.Request(endpoint+'/api/generate',json.dumps({'model':'gpt-oss:20b','prompt':'','keep_alive':'30m','options':{'num_ctx':32768,'num_thread':12}}).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=240) as r:warmup=json.load(r)
    results=[];start=time.monotonic()
    for index,ident in enumerate((2382,2353,2382,2353)):
        _,prompt,source,function=rows[ident]
        stamp=time.monotonic()
        text,meta=llm.generate(endpoint,'gpt-oss:20b',prompt,timeout=240,num_thread=12,
            num_predict=6000,think='low',temperature=.35,seed=20270904+ident,
            response_schema=modelrepair.EDIT_SCHEMA,num_ctx=32768 if args.mode=='fixed' else None)
        row={'proposal_id':ident,'function':function,'wall_seconds':time.monotonic()-stamp,'metadata':meta,'text':text}
        try:
            candidate=modelrepair.apply_proposal(source,modelrepair.parse_proposal(text,source=source))
            path=Path(__file__).with_name(f'{args.mode}-{index}.c');path.write_text(candidate)
            row.update(applied=True,candidate_file=str(path),candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest())
        except ValueError as exc:row.update(applied=False,error=str(exc))
        results.append(row)
        print(json.dumps({'mode':args.mode,'index':index,'seconds':row['wall_seconds'],
              'context':meta['_request_options']['num_ctx'],'load_seconds':meta.get('load_duration',0)/1e9,
              'tokens':meta.get('eval_count'),'applied':row['applied']}),flush=True)
        Path(__file__).with_name(args.mode+'.json').write_text(json.dumps({'mode':args.mode,
             'warmup':warmup,'seconds':time.monotonic()-start,'results':results},indent=2))

if __name__=='__main__':main()

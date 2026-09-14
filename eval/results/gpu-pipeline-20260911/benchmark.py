"""Development-only replay of saved prompts; never imports candidates into the campaign."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver.modelrepair import EDIT_SCHEMA

def post(endpoint,path,body):
    req=urllib.request.Request(endpoint+path,json.dumps(body).encode(),{'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=240) as response:return json.load(response)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--endpoint',default='http://127.0.0.1:11434')
    p.add_argument('--label',required=True)
    p.add_argument('--parallel',type=int,default=1)
    p.add_argument('--fixed-tokens',type=int,default=0)
    args=p.parse_args()
    with sqlite3.connect('file:'+str(ROOT/'eval/results/resume-pipeline-20260908/campaign.sqlite')+'?mode=ro',uri=True) as c:
        rows=c.execute('SELECT id,prompt_context,prompt_sha256 FROM model_proposals WHERE id IN (2380,2381,2382,2383) ORDER BY id').fetchall()
    if args.fixed_tokens:
        rows=[rows[-1]]*4
    samples=[]; stop=threading.Event()
    def monitor():
        while not stop.is_set():
            out=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:samples.append([time.time(),*[float(v.strip()) for v in out.stdout.split(',')]])
            except ValueError:pass
            stop.wait(.5)
    # Establish 32k allocation before timing, matching the longest live requests.
    post(args.endpoint,'/api/generate',{'model':'gpt-oss:20b','prompt':'','keep_alive':'30m','options':{'num_ctx':32768}})
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    def one(row):
        ident,prompt,sha=row
        start=time.monotonic()
        response=post(args.endpoint,'/api/chat',{'model':'gpt-oss:20b','messages':[{'role':'user','content':prompt}], 'think':'high' if args.fixed_tokens else 'low','stream':False,'format':EDIT_SCHEMA,'keep_alive':'30m','options':{'num_ctx':32768,'num_predict':args.fixed_tokens or 6000,'temperature':.35,'seed':20270904+ident}})
        text=response['message']['content']
        try:
            parsed=json.loads(text)
            valid=isinstance(parsed,dict) and all(k in parsed for k in EDIT_SCHEMA['required']) and isinstance(parsed.get('edits'),list)
        except ValueError:valid=False
        return {'proposal_id':ident,'prompt_sha256':sha,'wall_seconds':time.monotonic()-start,'shape_valid':valid,'response':response}
    start=time.monotonic()
    try:
        with ThreadPoolExecutor(args.parallel) as pool:results=list(pool.map(one,rows))
    finally:stop.set();thread.join()
    elapsed=time.monotonic()-start
    report={'label':args.label,'endpoint':args.endpoint,'parallel':args.parallel,'seconds':elapsed,'results':results,'gpu_samples':samples,'total_generated_tokens':sum(r['response'].get('eval_count',0) for r in results)}
    (Path(__file__).parent/(args.label+'.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in {'results','gpu_samples'}}),flush=True)
    print([(r['proposal_id'],r['shape_valid'],r['response'].get('done_reason'),r['response'].get('prompt_eval_count'),r['response'].get('eval_count')) for r in results],flush=True)

if __name__=='__main__':main()

"""Prepared, opt-in backend microprobe. Default invocation never touches servers.

Execution requires a paused campaign and explicit --execute. This compares two
llama.cpp modes, not llama.cpp against Ollama; accepted edits are not compiled or
imported into the campaign. No model download or draft model is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import modelrepair

RUN=ROOT/'eval/results/resume-pipeline-20260908'
SERVER=Path('C:/Users/grant/AppData/Local/Programs/Ollama/lib/ollama/llama-server.exe')
WEIGHTS=Path('C:/Users/grant/.ollama/models/blobs/sha256-e7b273f9636059a689e3ddcab3716e4f65abe0143ac978e46673ad0e52d09efb')
OLLAMA='http://127.0.0.1:11435'
BACKEND='http://127.0.0.1:11436'


def request(endpoint,path,body=None,timeout=20,text=False):
    req=urllib.request.Request(endpoint+path,data=None if body is None else json.dumps(body).encode(),
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as stream:
        raw=stream.read().decode()
        return raw if text else json.loads(raw)


def save(path,value):
    temporary=path.with_suffix(path.suffix+'.new')
    temporary.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def paused():
    service=json.loads((RUN/'service.json').read_bytes())
    if service.get('status')!='paused' or service.get('worker_pid'):
        raise RuntimeError('Campaign must be paused with no controller worker before this probe')
    pointer=json.loads((RUN/'campaign.json').read_bytes())
    if pointer.get('health',{}).get('parallel_inflight'):
        raise RuntimeError('Campaign has unfinished private jobs')


def gpu_sample():
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.total,power.draw',
             '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,
             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
        return {'at':time.time(),'values':[float(v.strip()) for v in result.stdout.splitlines()[0].split(',')]}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'at':time.time(),'unavailable':True}


def log_tail(path):
    if not path.exists():return ''
    with path.open('rb') as stream:
        stream.seek(0,2);stream.seek(max(0,stream.tell()-256*1024))
        return stream.read().decode(errors='replace')


def resource_problem(text):
    if re.search(r'no usable GPU found',text,re.I):
        return 'Standalone backend did not find a usable GPU'
    if re.search(r'(out of memory|cudaMalloc.*failed|failed to allocate.*CUDA)',text,re.I):
        return 'GPU allocation failure in server log'
    offload=re.findall(r'offloaded\s+(\d+)\s*/\s*(\d+)\s+layers',text,re.I)
    if offload and int(offload[-1][0])<int(offload[-1][1]):
        return 'Partial model layer offload; backend probe requires full GPU residency'
    return None


def stop_owned(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=15)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--out',type=Path,default=Path(__file__).with_name('speculative-microprobe'))
    parser.add_argument('--restore-options-json',default='{"num_ctx":32768,"num_thread":12}')
    args=parser.parse_args()
    if os.name!='nt':raise SystemExit('Run with Windows Python; this executable uses the Windows GPU backend')
    restore_options=json.loads(args.restore_options_json)
    if restore_options.get('num_ctx')!=32768:raise ValueError('Restore must retain the live fixed 32k context')
    args.out.mkdir(parents=True,exist_ok=True)
    if (args.out/'result.json').exists():raise SystemExit('Results already exist; select a fresh --out directory')
    with sqlite3.connect('file:'+str(RUN/'campaign.sqlite')+'?mode=ro',uri=True) as conn:
        rows={row[0]:row for row in conn.execute('SELECT p.id,p.prompt_context,a.source_code,f.name '
              'FROM model_proposals p JOIN attempts a ON a.id=p.parent_attempt_id '
              'JOIN functions f ON f.addr=a.func_addr WHERE p.id IN (2353,2382)')}
    if set(rows)!={2353,2382}:raise RuntimeError('Saved benchmark prompts unavailable')
    if not SERVER.is_file() or not WEIGHTS.is_file():raise RuntimeError('Existing local backend/model unavailable')
    cuda_directory=SERVER.parent/'cuda_v13'
    backend_env=dict(os.environ)
    backend_env['GGML_BACKEND_PATH']=str(cuda_directory/'ggml-cuda.dll')
    backend_env['PATH']=str(SERVER.parent)+os.pathsep+str(cuda_directory)+os.pathsep+backend_env.get('PATH','')
    help_text=subprocess.run([str(SERVER),'--help'],capture_output=True,text=True,timeout=15,
                             creationflags=subprocess.CREATE_NO_WINDOW,check=True,env=backend_env).stdout
    for flag in ('--spec-type','ngram-simple','--no-cache-prompt','--reasoning-effort','--offline'):
        if flag not in help_text:raise RuntimeError('Installed server lacks '+flag)
    common=[str(SERVER),'--model',str(WEIGHTS),'--host','127.0.0.1','--port','11436',
            '--gpu-layers','all','--flash-attn','on','--parallel','1','--ctx-size','32768',
            '--cache-type-k','f16','--cache-type-v','f16','--threads','12','--threads-batch','12',
            '--batch-size','512','--ubatch-size','512','--reasoning-effort','low',
            '--no-cache-prompt','--metrics','--perf','--offline','--verbose']
    plan={'scope':'bounded model-free speculative backend microprobe; no campaign import or compile verdict',
          'commands':[common+['--spec-type',mode] for mode in ('none','ngram-simple')],
          'sequence':[2382,2353,2382,2353],'max_tokens':2048,'temperature':.35,
          'seed_formula':'20270904+proposal_id','schema':modelrepair.EDIT_SCHEMA,
          'prompt_sha256':{str(i):hashlib.sha256(rows[i][1].encode()).hexdigest() for i in rows},
          'weight_file':{'path':str(WEIGHTS),'size':WEIGHTS.stat().st_size},
          'backend_library':backend_env['GGML_BACKEND_PATH'],
          'restore_options':restore_options,
          'documentation':'https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md'}
    save(args.out/'plan.json',plan)
    (args.out/'server-help.txt').write_text(help_text,encoding='utf-8')
    if not args.execute:
        print(json.dumps({'prepared':str(args.out),'executed':False,'requests':8,'max_tokens_each':2048}),flush=True)
        return
    paused()
    with socket.socket() as guard:
        guard.bind(('127.0.0.1',11436))  # Refuse to commandeer an occupied endpoint.
    report={'plan':plan,'started_at':time.time(),'arms':[],'restoration':None}
    process=None;unload_started=False
    try:
        report['ollama_before']=request(OLLAMA,'/api/ps')
        unload_started=True
        report['unload']=request(OLLAMA,'/api/generate',{'model':'gpt-oss:20b','keep_alive':0,'stream':False},timeout=90)
        deadline=time.monotonic()+60
        while request(OLLAMA,'/api/ps').get('models'):
            if time.monotonic()>deadline:raise RuntimeError('Ollama did not release all loaded models')
            time.sleep(.5)
        for mode in ('none','ngram-simple'):
            paused()
            arm={'mode':mode,'results':[],'gpu':[],'command':common+['--spec-type',mode]}
            report['arms'].append(arm)
            stdout_path=args.out/(mode+'-stdout.log');stderr_path=args.out/(mode+'-stderr.log')
            stop=threading.Event();alert=[]
            with stdout_path.open('wb') as stdout,stderr_path.open('wb') as stderr:
                process=subprocess.Popen(arm['command'],stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                                         creationflags=subprocess.CREATE_NO_WINDOW,env=backend_env)
                arm['owned_pid']=process.pid;started=time.monotonic()
                def monitor():
                    while not stop.is_set():
                        arm['gpu'].append(gpu_sample())
                        problem=resource_problem(log_tail(stderr_path)+log_tail(stdout_path))
                        if problem:
                            alert.append(problem)
                            if process.poll() is None:process.terminate()
                            return
                        stop.wait(2)
                monitor_thread=threading.Thread(target=monitor,daemon=True);monitor_thread.start()
                try:
                    while True:
                        if process.poll() is not None:raise RuntimeError('Backend exited: '+str(alert or process.returncode))
                        if time.monotonic()-started>180:raise TimeoutError('Backend failed to become ready within 180 seconds')
                        try:
                            health=request(BACKEND,'/health',timeout=2)
                            if health.get('status')=='ok':break
                        except (OSError,urllib.error.URLError,ValueError):pass
                        time.sleep(.5)
                    arm['load_seconds']=time.monotonic()-started
                    arm['props']=request(BACKEND,'/props')
                    template=arm['props'].get('chat_template')
                    arm['template_sha256']=hashlib.sha256(json.dumps(template,sort_keys=True).encode()).hexdigest()
                    if template is None:raise RuntimeError('Backend did not expose its chat template for comparison')
                    if len(report['arms'])>1 and arm['template_sha256']!=report['arms'][0]['template_sha256']:
                        raise RuntimeError('Backend chat template differs between arms')
                    logs=log_tail(stderr_path)+log_tail(stdout_path)
                    if not re.search(r'offloaded\s+(\d+)\s*/\s*\1\s+layers',logs,re.I):
                        raise RuntimeError('Full GPU layer residency was not confirmed in backend logs')
                    for index,ident in enumerate(plan['sequence']):
                        paused()
                        if alert:raise RuntimeError(alert[0])
                        _,prompt,source,function=rows[ident]
                        body={'model':'local-gpt-oss','messages':[{'role':'user','content':prompt}],
                              'stream':False,'max_tokens':2048,'temperature':.35,'seed':20270904+ident,
                              'reasoning_effort':'low','cache_prompt':False,'timings_per_token':True,
                              'response_format':{'type':'json_schema','json_schema':{
                                  'name':'repair_edit','strict':True,'schema':modelrepair.EDIT_SCHEMA}}}
                        metrics_before=request(BACKEND,'/metrics',text=True)
                        stamp=time.monotonic()
                        response=request(BACKEND,'/v1/chat/completions',body,timeout=180)
                        entry={'proposal_id':ident,'function':function,'index':index,
                               'wall_seconds':time.monotonic()-stamp,'raw_response':response}
                        arm['results'].append(entry)
                        metrics_after=request(BACKEND,'/metrics',text=True)
                        (args.out/f'{mode}-{index}-metrics-before.txt').write_text(metrics_before)
                        (args.out/f'{mode}-{index}-metrics-after.txt').write_text(metrics_after)
                        text=response['choices'][0]['message'].get('content') or ''
                        try:
                            candidate=modelrepair.apply_proposal(source,modelrepair.parse_proposal(text,source=source))
                            path=args.out/f'{mode}-{index}.c';path.write_text(candidate)
                            entry.update(applied=True,candidate_file=str(path),candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest())
                        except ValueError as exc:entry.update(applied=False,parse_error=str(exc))
                        entry['speculative_metrics']=[line for line in metrics_after.splitlines()
                                                      if not line.startswith('#') and re.search('draft|accept|specul',line,re.I)]
                        save(args.out/'result.json',report)
                        print(json.dumps({'mode':mode,'index':index,'seconds':entry['wall_seconds'],
                                          'applied':entry['applied'],'usage':response.get('usage')}),flush=True)
                finally:
                    stop.set();monitor_thread.join(timeout=5)
                    stop_owned(process);process=None
                    arm['resource_alerts']=alert
                    arm['speculative_log_lines']=[line for line in (log_tail(stderr_path)+log_tail(stdout_path)).splitlines()
                                                 if re.search('draft|accept|specul|offloaded|buffer size',line,re.I)]
                    save(args.out/'result.json',report)
    except BaseException as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        stop_owned(process)
        if unload_started:
            try:
                restored=request(OLLAMA,'/api/generate',{'model':'gpt-oss:20b','prompt':'','stream':False,
                                  'keep_alive':'30m','options':restore_options},timeout=240)
                loaded=request(OLLAMA,'/api/ps')
                if not any(m.get('context_length')==32768 and m.get('name')=='gpt-oss:20b' for m in loaded.get('models',[])):
                    raise RuntimeError('Restored Ollama model did not confirm 32k context')
                report['restoration']={'ok':True,'response':restored,'models':loaded}
            except BaseException as exc:
                report['restoration']={'ok':False,'error':f'{type(exc).__name__}: {exc}'}
                print('RESTORATION FAILED: '+str(exc),file=sys.stderr,flush=True)
        report['finished_at']=time.time();save(args.out/'result.json',report)
        if (report.get('restoration') or {}).get('ok') is False:
            raise RuntimeError('Ollama restoration failed; inspect result.json before resuming the campaign')


if __name__=='__main__':main()

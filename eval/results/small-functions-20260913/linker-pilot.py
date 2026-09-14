"""Fresh source-bound builds and isolated integration; no live imports or reference-source inference."""
from contextlib import closing
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import time

from eval import campaign_runtime, campaign_state, integration_gate, prepare_integration
from solver import function_boundary

ROOT=Path('/mnt/c/Code/gameDecomp')
RUN=ROOT/'eval/results/resume-pipeline-20260908'
REPO=Path('/home/grant/decomp/sbk1')
OUT=ROOT/'eval/results/small-functions-20260913'/('linker-pilot-'+str(time.time_ns()))
OUT.mkdir()
raw=(RUN/'campaign.json').read_bytes()
pointer=json.loads(raw)
with closing(sqlite3.connect((RUN/pointer['store']).as_uri()+'?mode=ro',uri=True)) as conn:
    state=campaign_state._hydrate(conn,pointer)
(OUT/'pointer.json').write_bytes(raw)
spec=importlib.util.spec_from_file_location('frozen_function_boundary',RUN/'code/solver/function_boundary.py')
frozen=importlib.util.module_from_spec(spec)
spec.loader.exec_module(frozen)
report={'checkpoint':pointer['commit'],'pointer_sha256':hashlib.sha256(raw).hexdigest(),
        'boundary_sha256':hashlib.sha256(Path(function_boundary.__file__).read_bytes()).hexdigest(),
        'frozen_boundary_sha256':hashlib.sha256(Path(frozen.__file__).read_bytes()).hexdigest(),
        'functions':{},'live_mutated':False}
for name in ('osCreateMesgQueue','fadeOutMultiplayerCourseSelectMenu','drawRaceMotionAnimationDebugViewerMotionNumber'):
    node=state['nodes'][name]
    folder=OUT/name
    folder.mkdir()
    item={'source_binding':campaign_runtime.binding(node),'source':node['source'],'status':node['status']}
    report['functions'][name]=item
    try:
        isolated,ws,candidate,_,_=campaign_runtime.compile_candidate(repo=REPO,db=Path(state['config']['db']),function=name,node=node,folder=folder)
        att=candidate.attempt
        certificate=att.verification or {}
        item.update(score=att.score,object_exact=att.exact,frontend=att.frontend,
                    private_attempt_id=att.receipt_id,workspace=str(ws),source_sha256=hashlib.sha256(candidate.source.encode()).hexdigest())
        kwargs=dict(target=ws/'target.o',candidate=candidate.object_path,assembly=ws/'target.s',rom=REPO/'snowboardkids.z64',
                    config=REPO/'snowboardkids.yaml',symbols=REPO/'symbol_addrs.txt',function=name,address=node['address'],size=node['size'])
        item['frozen_boundary']=frozen.certify(**kwargs)
        item['main_boundary']=function_boundary.certify(**kwargs)
        if item['main_boundary']['function_exact']:
            item['main_revalidation']=function_boundary.revalidate(item['main_boundary'])
            # workspace.score's authentic certificate includes its isolated paths;
            # preserve them, not this separate canonical-path diagnostic receipt.
            entry={'function':name,'source':str(folder/'selected.c'),'attempt_id':att.receipt_id,'verification':certificate}
            manifest=prepare_integration.prepare(repo=REPO,db=folder/'attempts.sqlite',entries=[entry],output_dir=folder/'prepared')
            item['integration']=integration_gate.run(repo=REPO,manifest=manifest,output=folder/'integration.json')
        else:
            item['integration']={'status':'not_admitted','reason':item['main_boundary'].get('error')}
    except Exception as exc:
        item['error']=type(exc).__name__+': '+str(exc)
    (OUT/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'function':name,'score':item.get('score'),'frozen':item.get('frozen_boundary',{}).get('function_exact'),
                      'main':item.get('main_boundary',{}).get('function_exact'),
                      'integration':item.get('integration',{}).get('status'),'error':item.get('error')}),flush=True)
print(str(OUT/'report.json'),flush=True)

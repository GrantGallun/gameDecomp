"""Three approaches, three saved failures, two-call cap per approach/parent."""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3
import time

from eval import agentrepair, frozen_wavefront
from kb import attempts
from solver import llm, modelrepair, patch_protocols as protocols, plateau, repair, workspace

ROOT = Path(__file__).resolve().parents[1]
ARMS = ('slots_only', 'two_stage', 'focused_retry')


def main():
    prior = ROOT / 'eval/results/patch-guidance-pilot-20260910-v1'
    out = ROOT / 'eval/results/patch-protocol-pilot-20260910-v1'
    out.mkdir(exist_ok=False)
    previous = json.loads((prior / 'report.json').read_text())
    selected = [c for c in previous['cases'] if c['arms']['guidance']['status'] == 'invalid']
    assert len(selected) == 3
    endpoint, model = llm.host(), 'gpt-oss:20b'
    report = {'status':'running', 'kind':'three-patch-protocols-dev',
        'selection':'the three failures remaining in the saved early-guidance pilot',
        'model':model, 'model_digest':frozen_wavefront.model_digest(endpoint,model),
        'config':{'calls_per_arm_cap':2,'num_predict_per_call':4096,'temperature':.35,
                  'think':'low','num_thread':4,'timeout':240},
        'scope':'failure-enriched DEV; two-stage spends one call selecting locations; not equal numbers of code proposals',
        'integration_requested':False, 'cases':[],
        'code_hashes':{p:repair._digest((ROOT/p).read_text()) for p in
                       ('solver/patch_protocols.py','solver/modelrepair.py','eval/patch_protocol_pilot.py')}}
    def save():
        pending=out/'report.pending.json'
        pending.write_text(json.dumps(report,indent=2))
        pending.replace(out/'report.json')
    for c in selected:
        function=c['function']
        agentrepair._refuse_frozen_heldout(ROOT/'eval/sets',function)
        folder=out/function
        folder.mkdir()
        source=(prior/function/'original.c').read_text()
        assert repair._digest(source)==c['source_sha256']
        shutil.copy2(prior/function/'original.c',folder/'original.c')
        shutil.copy2(prior/function/'baseline.prompt.txt',folder/'original.prompt.txt')
        report['cases'].append({'function':function,'source_sha256':c['source_sha256'],
            'seed':c['seed'],'prior_rejected':c['arms']['guidance']['response'],
            'prior_error':c['arms']['guidance'].get('error','invalid proposal'),'arms':{}})
    save()
    for index,case in enumerate(report['cases']):
        function=case['function']
        folder=out/function
        source=(folder/'original.c').read_text()
        original_prompt=(folder/'original.prompt.txt').read_text()
        repo=folder/'repo'
        repo.mkdir()
        original=Path('/home/grant/decomp/sbk1')
        for name in ('tools','include','src','asm','.venv','Makefile','symbol_addrs.txt',
                     'snowboardkids.yaml','snowboardkids.z64','build',
                     'undefined_syms_auto.txt','undefined_syms.txt'):
            path=original/name
            if path.exists():
                (repo/name).symlink_to(path,target_is_directory=path.is_dir())
        ws=repo/'nonmatchings'/function
        ws.mkdir(parents=True)
        for path in (original/'nonmatchings'/function).iterdir():
            if path.is_file() and (path.suffix=='.py' or path.name.startswith('target') or
                    path.name in {'build.sh','base.c','prelude.inc','.diff_algorithm'}):
                shutil.copy2(path,ws/path.name)
        db=sqlite3.connect(folder/'attempts.sqlite')
        baseline=sqlite3.connect((ROOT/'eval/results/kb-sbk1-rom-ranges-v1.sqlite').as_uri()+'?mode=ro',uri=True)
        baseline.backup(db)
        baseline.close()
        parent=workspace.score(ws,repo,'parent',source,conn=db,func=function,
                               strategy='patch-protocol-pilot-parent')
        case['parent']=asdict(parent)
        order=ARMS[index:]+ARMS[:index]
        for arm in order:
            records=[]
            case['arms'][arm]=records
            choice=None
            rejected,error=case['prior_rejected'],case['prior_error']
            for step in range(2):
                selecting=arm=='two_stage' and choice is None
                allowed=list(protocols.locations(source))
                if arm=='focused_retry':
                    prompt,allowed=protocols.focused(source,rejected,error)
                else:
                    prompt=protocols.slot_prompt(original_prompt)
                    if step and arm=='slots_only':
                        prompt+='\nCURRENT C is still the original parent. Previous trial was not applied.\n'+error+'\nREJECTED TRIAL:\n'+rejected
                if selecting:
                    prompt=('FIRST STAGE: choose 1-4 valid CURRENT C locations only. '
                            'Do not generate replacement code yet. Return {"slots":["L..."],"reason":"..."}.\n'
                            +prompt)
                    schema=protocols.selection_schema(allowed)
                elif choice is not None:
                    allowed=list(choice.slots)
                    prompt=('SECOND STAGE: emit replacement code only at these preselected locations: '
                            +json.dumps(allowed)+'. No other locations are permitted.\n'+prompt)
                    schema=protocols.patch_schema(allowed)
                else:
                    schema=protocols.patch_schema(allowed)
                workspace.assert_uncontaminated(prompt,repo,function)
                tag=f'{arm}_{step}'
                (folder/(tag+'.prompt.txt')).write_text(prompt)
                row={'stage':'select' if selecting else 'patch','status':'generation_error',
                     'seed':case['seed']+step,'prompt_sha256':repair._digest(prompt),
                     'allowed_slots':allowed}
                records.append(row)
                save()
                candidate=None
                started=time.monotonic()
                try:
                    response,meta=llm.generate(endpoint,model,prompt,timeout=240,think='low',
                        num_thread=4,temperature=.35,num_predict=4096,seed=row['seed'],
                        response_schema=schema,cache_dir=out/'cache',
                        cache_namespace='patch-protocol-pilot-20260910-v1')
                    row.update(response=response,meta=meta,status='invalid')
                    (folder/(tag+'.response.txt')).write_text(response)
                    if meta.get('done_reason')=='length':
                        row['status']='incomplete'
                    elif selecting:
                        choice=protocols.select(source,response)
                        row.update(status='selection_valid',selection=asdict(choice))
                    else:
                        candidate=protocols.apply(source,response,allowed=allowed,selection=choice)
                        row['status']='application_valid'
                except Exception as exc:
                    row['error']=f'{type(exc).__name__}: {exc}'
                row['generation_seconds']=time.monotonic()-started
                pid=attempts.record_model_proposal(db,run_id='patch-protocol-pilot',
                    parent_attempt_id=parent.receipt_id,prompt=prompt,
                    raw_response=row.get('response',row.get('error','')),status=row['status'],
                    model=model,kind=arm,sampling={'seed':row['seed'],'stage':row['stage'],'meta':row.get('meta',{})},
                    wall_ms=int(row['generation_seconds']*1000),
                    token_cost=int(row.get('meta',{}).get('eval_count',0)))
                if candidate is not None:
                    (folder/(tag+'.c')).write_text(candidate)
                    att=workspace.score(ws,repo,tag,candidate,conn=db,func=function,
                        strategy='patch-protocol-pilot:'+arm,parent_attempt_id=parent.receipt_id)
                    attempts.link_model_proposal(db,pid,att.receipt_id)
                    row.update(compiled=att.compiled,frontend_pass=(att.frontend or {}).get('passed') is True,
                               exact=plateau.verified(repair._State(candidate,att)),score=att.score,attempt=asdict(att))
                    error='Candidate compile feedback (candidate was NOT applied):\n'+att.compiler_stderr+'\n'+(att.frontend or {}).get('diagnostics','')
                else:
                    error=row.get('error',row['status'])
                rejected=row.get('response',rejected)
                save()
                print(json.dumps({'function':function,'arm':arm,'step':step,'stage':row['stage'],
                    'status':row['status'],'compiled':row.get('compiled'),
                    'frontend_pass':row.get('frontend_pass'),'exact':row.get('exact'),'error':row.get('error')}),flush=True)
        db.close()
    report['status']='complete'
    save()


if __name__=='__main__':
    main()

"""Bounded actual-model repair comparison; no production wiring or training.

All scheduled functions stay in the denominator. The same mapped lifting
observations select all three arms. Both model arms use the existing repair
loop, identical sampling seeds and a maximum of three root-level proposals.
The deterministic arm offers at most one frozen byte-address redraft.
"""
from pathlib import Path
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADAPTER = ROOT / 'eval/results/m2c-reconstruction-20260930'
sys.path[:0] = [str(ROOT), str(ADAPTER)]
from solver import m2c_uncertainty as uncertainty, modelrepair, workspace, llm
from solver import repair_context, m2c_byte_view, m2c_placeholders, byte_certificate
from byte_address import ByteAddresses
from m2c import main


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def seed_observation(conn, folder, result, source):
    reports = json.loads((folder/'generation.json').read_text())
    ancestor = result['attempt_id']
    visited = set()
    while ancestor is not None and ancestor not in visited:
        visited.add(ancestor)
        row = conn.execute('SELECT source_sha256,parent_attempt_id FROM attempts WHERE id=?', (ancestor,)).fetchone()
        if row is None:
            break
        matching = [r for r in reports if r.get('source_sha256') == row[0] and r.get('observer_calls')]
        if matching:
            # Keep the first actual derivation, never select by a future outcome.
            call = matching[0]['observer_calls'][0]
            observation_folder = Path(call['provenance_path']).parent
            stdout = (observation_folder/'m2c.stdout').read_text()
            report = uncertainty.rebind(source, call['uncertainty'], origin_source=stdout)
            report['generation_parent_attempt_id'] = ancestor
            return report, observation_folder
        ancestor = row[1]
    return {'status':'unavailable-lineage', 'source_sha256':uncertainty.sha(source),
            'hazards':[], 'errors':[], 'omitted_hazards':0}, None


def byte_redraft(folder, source, function):
    """Same binary/public context as the witnessed parent, valid syntax only."""
    output = io.StringIO()
    with ByteAddresses() as adapter, contextlib.redirect_stdout(output):
        status = main.run(main.parse_flags(['--target','mips-ido-c','--no-cache','--valid-syntax',
            '--context',str(folder/'context.c'),str(folder/'input.s')]))
    if status:
        return None, {'status':'translation-failed', 'returncode':status}
    try:
        lowered = m2c_byte_view.lower(output.getvalue(), function, target_assembly=(folder/'input.s').read_text())
    except ValueError as exc:
        return None, {'status':'declined-unwitnessed-lowering','reason':str(exc)}
    text = lowered['source'] if isinstance(lowered,dict) else lowered
    original_definition, _ = repair_context.definition(source,function)
    definition,end = repair_context.definition(text,function)
    child = source[:original_definition.start()] + text[definition.start():end] + '\n'
    child,_ = m2c_placeholders.rewrite(child)
    return child, {'status':'generated', 'byte_changes':adapter.changes,
                   'late_store_views':adapter.store_views, 'source_sha256':uncertainty.sha(child),
                   'context_sha256':sha((folder/'context.c').read_bytes()),
                   'assembly_sha256':sha((folder/'input.s').read_bytes())}


def run(repo, parent, output, endpoint):
    output.mkdir(parents=True,exist_ok=False)
    selection = json.loads((parent/'selection.json').read_text())
    functions = selection['functions']
    census = json.loads((ROOT/'eval/results/joint-reconstruction-20260930/census.json').read_text())
    assert not set(functions)&set(census['heldout'])
    model = 'gpt-oss:20b'
    models = json.load(urllib.request.urlopen(endpoint+'/api/tags',timeout=10))['models']
    identity = next(m for m in models if m['name']==model)
    files = [Path(__file__), HERE/'panel.py', HERE/'prepare.py', ROOT/'solver/m2c_uncertainty.py',
             ROOT/'solver/m2c_source_binding.py', ROOT/'solver/function_boundary.py', ROOT/'solver/byte_certificate.py', ROOT/'solver/modelrepair.py', ROOT/'solver/workspace.py', ROOT/'solver/llm.py',
             ADAPTER/'byte_address.py', ROOT/'solver/repair_diagnostic.py']
    pins = {}
    for path in files:
        relative = path.relative_to(ROOT)
        destination = output/'code'/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,destination)
        pins[str(relative)] = sha(path.read_bytes())
    write(output/'preregistration.json', {'functions':functions,'selection':selection,
        'trigger':'source-bound surviving expression plus an independently verified original target instruction word; shared by all arms',
        'arms':['ordinary-model','uncertainty-model','deterministic-byte'],
        'model':model,'model_digest':identity['digest'],'endpoint':endpoint,
        'max_model_calls_per_function_per_model_arm':3,'max_child_compiles_per_arm':3,
        'model_depth':1,'draws':3,'temperature':0.4,'think':'low','num_predict':1800,'timeout_seconds':180,
        'deterministic_redrafts':1,'baseline_retained':True,'model_guidance_scope':'address and pointer-store uncertainty only',
        'primary':'compiler and strict frontend passing retained seeds',
        'secondary':['independent byte equality','attempt cost','proposal failures','trigger coverage'],
        'semantics':'no differential execution or full campaign continuation in this probe',
        'training_eligible':False,'production_wiring':False,'code_sha256':pins})
    db = output/'attempts.sqlite'
    with sqlite3.connect(parent/'attempts.sqlite') as original, sqlite3.connect(db) as destination:
        original.backup(destination)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    original_score, original_prompt = workspace.score, modelrepair.build_prompt
    rows, attempts = [], []
    for fn in functions:
        seed_folder = parent/'intake'/fn/'current'
        intake = json.loads((seed_folder/'result.json').read_text())
        if not intake.get('attempt_id') or not intake.get('source'):
            rows.append({'function':fn,'group':selection['groups'][fn], 'status':'no-complete-intake-seed',
                         'intake_status':intake['status'],'arms':[]})
            write(output/'comparison.partial.json',{'rows':rows,'new_attempts':attempts})
            continue
        source = conn.execute('SELECT source_code FROM attempts WHERE id=?',(intake['attempt_id'],)).fetchone()[0]
        assert uncertainty.sha(source) == intake['source_sha256']
        report, observation_folder = seed_observation(conn,seed_folder,intake,source)
        hazards=report.get('hazards',[])
        verified=[r for r in hazards if (r.get('instruction') or {}).get('byte_attribution_status')=='verified-target-word']
        report={**report,'hazards':verified,'excluded_unverified_hazards':len(hazards)-len(verified)}
        packet = uncertainty.packet(source,report)
        write(output/'observations'/fn/'report.json',report)
        write(output/'observations'/fn/'packet.json',packet)
        arm_rows = []
        for arm in ('ordinary-model','uncertainty-model','deterministic-byte'):
            folder = output/'repair'/fn/arm
            folder.mkdir(parents=True)
            for name in ('target.s','target.o','build.sh','objdump.py','dist.py','normalize_asm.py','prelude.inc'):
                shutil.copy2(seed_folder/name,folder/name)
            calls = []
            started = time.monotonic()

            def score(ws, repo_arg, tag, code, **kwargs):
                if len(calls)>=4:
                    raise RuntimeError('fixed arm compile budget exhausted')
                extra = {**(kwargs.pop('extra',None) or {}),'training_eligible':False,
                         'uncertainty_experiment_arm':arm, 'production_wiring':False,
                         'observation_sha256':sha(json.dumps(report,sort_keys=True).encode())}
                att = original_score(ws,repo_arg,tag,code,**kwargs,extra=extra)
                row = {'function':fn,'arm':arm,'attempt_id':att.receipt_id,
                       'source_sha256':uncertainty.sha(code),'compiled':att.compiled,
                       'frontend_passed':(att.frontend or {}).get('passed') is True,
                       'exact':workspace.repair_complete(att),'score':att.score,
                       'parent_attempt_id':kwargs.get('parent_attempt_id'),
                       'source_path':str(ws/(tag+'.c')),'object_path':str(ws/(tag+'.o'))}
                if att.compiled:
                    row['certificate'] = byte_certificate.certify(ws/'target.o',ws/(tag+'.o'),source=code)
                calls.append(row);attempts.append(row)
                write(output/'attempts.json',attempts)
                return att

            def guided_prompt(asm, code, attempt, **kwargs):
                prompt = original_prompt(asm,code,attempt,**kwargs)
                return prompt + (uncertainty.render(code,report) if arm=='uncertainty-model' else '')

            workspace.score, modelrepair.build_prompt = score, guided_prompt
            model_calls, status, error = 0, 'retained', None
            best_source, best_attempt = source, None
            try:
                base = score(folder,repo,fn+'_root',source,conn=conn,func=fn,
                    strategy='uncertainty-probe-root:'+arm,run_id=fn+':'+arm,
                    parent_attempt_id=intake['attempt_id'],relation='root-reverification')
                best_attempt = base
                if packet['regions'] and not workspace.repair_complete(base):
                    if arm=='deterministic-byte':
                        child,detail = byte_redraft(observation_folder,source,fn)
                        write(folder/'redraft.json',detail)
                        if child is not None and child!=source:
                            attempt = score(folder,repo,fn+'_byte',child,conn=conn,func=fn,
                                strategy='uncertainty-probe-byte',run_id=fn+':'+arm,
                                parent_attempt_id=base.receipt_id,relation='byte-address-alternative',
                                extra={'transformation':detail})
                            if modelrepair._quality(attempt)>modelrepair._quality(base):
                                best_attempt,best_source = attempt,child
                            status = 'evaluated'
                        else:
                            status = 'duplicate-or-declined'
                    else:
                        seeds = tuple(int(sha((fn+':'+str(i)).encode())[:8],16)%2**31 for i in range(3))
                        result = modelrepair.search(repo,fn,source,folder,model=model,endpoint=endpoint,
                            conn=conn,base_attempt=base,
                            base_object_path=folder/(fn+'_root.o') if base.compiled else None,
                            draws=3,max_calls=3,max_depth=1,beam_width=3,timeout=180,
                            think='low',num_predict=1800,temperature=0.4,num_thread=12,
                            call_seeds=seeds,run_id=fn+':'+arm,structured_output=True,
                            resilient=False,retry_invalid=False,include_header_context=False)
                        model_calls = result.calls_attempted
                        best_attempt,best_source = result.best_attempt,result.best_source
                        write(folder/'search.json',{'log':result.log,'calls':model_calls,
                            'retained_attempt_id':best_attempt.receipt_id})
                        status = 'evaluated'
                elif not packet['regions']:
                    status = 'no-mapped-uncertainty'
            except Exception as exc:
                import traceback
                status,error = 'incomplete',repr(exc)
                write(folder/'error.json',{'error':error,'traceback':traceback.format_exc()})
            finally:
                workspace.score,modelrepair.build_prompt = original_score,original_prompt
            (folder/'retained.c').write_text(best_source)
            row = {'function':fn,'arm':arm,'status':status,'error':error,
                   'compiler_calls':len(calls),'model_calls':model_calls,
                   'retained_attempt_id':best_attempt.receipt_id if best_attempt else None,
                   'retained_source_sha256':uncertainty.sha(best_source),
                   'usable':bool(best_attempt and best_attempt.compiled and (best_attempt.frontend or {}).get('passed') is True),
                   'exact':bool(best_attempt and workspace.repair_complete(best_attempt)),
                   'score':best_attempt.score if best_attempt else None,'seconds':time.monotonic()-started}
            arm_rows.append(row)
            print(json.dumps(row),flush=True)
        rows.append({'function':fn,'group':selection['groups'][fn],
                     'triggered':bool(packet['regions']),'mapped_regions':len(packet['regions']),
                     'intake_status':intake['status'],'arms':arm_rows})
        write(output/'comparison.partial.json',{'rows':rows,'new_attempts':attempts})
    for path in files:
        assert sha(path.read_bytes())==pins[str(path.relative_to(ROOT))], 'experiment code changed'
    summary = {'scheduled':len(functions),'triggered':sum(r.get('triggered',False) for r in rows),
               'new_compiles':len(attempts),'new_model_proposals':conn.execute('SELECT COUNT(*) FROM model_proposals').fetchone()[0],
               'rows':rows,'training_eligible':False,'production_wiring':False}
    write(output/'comparison.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'}),flush=True)
    conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--endpoint')
    args=parser.parse_args()
    run(args.repo,args.parent,args.output,args.endpoint or llm.host())

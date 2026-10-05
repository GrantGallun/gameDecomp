"""Twelve preregistered final byte/store-view compiles, reusing frozen controls."""
from pathlib import Path
import argparse, json, shutil, sqlite3
from dataclasses import asdict
import run as trial

def execute(parent, output, repo):
    output.mkdir(parents=True,exist_ok=False)
    selection=json.loads((parent/'selection.json').read_text())
    trial.write(output/'preregistration.json',{'kind':'exploratory-late-store-view-followup',
        'parent':str(parent),'budget':len(selection['functions']),'arms':['byte-store-view'],
        'selection_reused_without_outcome_filtering':True,'training_eligible':False,
        'deployed':False,'code':{p.name:trial.sha(p.read_bytes()) for p in trial.HERE.glob('*.py')}})
    snapshots=output/'code'; snapshots.mkdir()
    for p in trial.HERE.glob('*.py'): shutil.copy2(p,snapshots/p.name)
    shutil.copy2(parent/'selection.json',output/'selection.json')
    shutil.copytree(parent/'targets',output/'targets')
    shutil.copytree(parent/'empty-context',output/'empty-context')
    shutil.copy2(parent/'environment.json',output/'environment.json')
    shutil.copy2(parent/'attempts.sqlite',output/'attempts.sqlite')
    identity=json.loads((output/'environment.json').read_text())
    old=json.loads((parent/'comparison.json').read_text())
    baselines={r['function']:r for r in old['rows'] if r['arm']=='baseline'}
    model=trial.bc.load(trial.bc.find_elf(repo),output/'binary-cache')
    conn=sqlite3.connect(output/'attempts.sqlite'); rows=[]
    for fn in selection['functions']:
        assembly=(output/'targets'/fn/'target.s').read_text()
        saved=trial.PRIOR/'portable/drafts'/fn/'joint/valid/context.json'
        ctx=json.loads(saved.read_text()) if saved.exists() else model.context(fn,assembly)
        trial.write(output/(fn+'-context.json'),ctx)
        source,gen=trial.generation(repo,fn,assembly,ctx,output/'drafts'/fn/'byte-store-view',True,())
        base=baselines[fn]
        base_source=(parent/'drafts'/fn/'baseline/source.c').read_text()
        row={'function':fn,'arm':'byte-store-view','generation':gen,'compiled':False,'frontend_passed':False,'exact':False}
        if source is not None:
            target=json.loads(Path(base['receipt']).read_text())['compile_target']
            compiler=trial.NativeCompiler(repo,{'function':fn,'compile_target':target,
                'target_object':f'targets/{fn}/target.o','context':'empty-context'},
                output,output/'compiles'/fn/'byte-store-view',budget=1,identity=identity)
            result=compiler(source,'byte-store-view',parent_source=base_source)
            measured=compiler.rows[-1]
            row.update(compiled=result.compiled,frontend_passed=(measured.get('frontend') or {}).get('passed') is True,
                exact=result.exact,source_sha256=measured['source_sha256'],error=measured.get('error'),
                receipt=str(compiler.output/measured['artifact']/'receipt.json'))
            att=trial.workspace.Attempt(result.compiled,0,result.exact,result.diff or '',measured.get('error') or '',
                '',verification=measured.get('verification'),frontend=measured.get('frontend'),compiler_recipe=compiler.recipe)
            row['attempt_id']=trial.workspace.record_attempt(conn,fn,source,att,
                strategy='m2c-lifting-dev-spike:byte-store-view',run_id=output.name,run_kind='dev-spike',
                parent_attempt_id=base['attempt_id'],wall_ms=int(measured['seconds']*1000),
                extra={'generation':gen,'training_eligible':False,'score_available':False})
            if result.compiled:
                row['faults']=asdict(trial.signals.analyse(result.diff,0))
                try: row['semantic']=trial.semantic(fn,compiler.target_dump,result.dump,source,ctx)
                except (OSError,ValueError,RuntimeError) as exc: row['semantic']={'status':'unavailable','error':str(exc)}
        rows.append(row); trial.write(output/'comparison.partial.json',{'rows':rows})
        print(json.dumps({k:row[k] for k in ('function','compiled','frontend_passed','exact')}),flush=True)
    summary={key:sorted(r['function'] for r in rows if r[key]) for key in ('compiled','frontend_passed','exact')}
    trial.write(output/'comparison.json',{'rows':rows,'summary':summary,'parent_attempts':48,'new_attempts':12,'deployed':False})
    conn.close(); print(json.dumps(summary),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--parent',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--repo',type=Path,default=Path('/home/grant/decomp/sbk1'))
    a=ap.parse_args(); execute(a.parent,a.output,a.repo)

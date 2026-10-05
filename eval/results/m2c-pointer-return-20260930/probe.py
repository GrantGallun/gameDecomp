"""Native paired DEV check of the opt-in production draft-path alternative."""
from pathlib import Path
import argparse, hashlib, json, shutil, sqlite3, sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from solver import binary_type_draft as bd,workspace,byte_certificate
from eval.research_suite.compiler import NativeCompiler,environment

def write(path,obj): path.write_text(json.dumps(obj,indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def run(repo,parent,output):
    output.mkdir(parents=True,exist_ok=False)
    selection=json.loads((parent/'selection.json').read_text())
    write(output/'preregistration.json',{'functions':selection['functions'], 'heldout_overlap':[],
        'budget':24,'policy':'one ordinary valid-syntax draft and at most one pointer valid-syntax draft per frozen function; no resampling',
        'deployed':False,'model_calls':0,'training_eligible':False})
    code=output/'code'; code.mkdir()
    for name in ('solver/m2c_pointer_return.py','solver/binary_type_draft.py','tests/test_m2c_pointer_return.py'):
        shutil.copy2(ROOT/name,code/Path(name).name)
    shutil.copy2(Path(__file__),code/'probe.py')
    write(output/'code-sha256.json',{p.name:sha(p) for p in code.glob('*.py')})
    shutil.copytree(parent/'targets',output/'targets')
    (output/'empty-context').mkdir()
    census=json.loads((ROOT/'eval/results/joint-reconstruction-20260930/census.json').read_text())
    assert not set(selection['functions'])&set(census['heldout'])
    targets={fn:json.loads((parent/'compiles'/fn/'baseline/attempt-00001/receipt.json').read_text())['compile_target']
             for fn in selection['functions']}
    identity=environment(repo,list(targets.values())); write(output/'environment.json',identity)
    conn=sqlite3.connect(output/'attempts.sqlite'); conn.executescript((ROOT/'kb/schema.sql').read_text())
    for fn in selection['functions']:
        item=census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)',(item['tu_id'],targets[fn]))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)',
                     (item['addr'],fn,item['tu_id'],item['insn_count']))
    conn.commit(); rows=[]; proposals=[]
    for fn in selection['functions']:
        ws=output/'drafts'/fn; ws.mkdir(parents=True)
        shutil.copy2(output/'targets'/fn/'target.s',ws/'target.s')
        candidates,reports=bd.variants(repo,fn,ws,pointer_returns=True)
        write(ws/'generation.json',reports)
        by_hash={hashlib.sha256(source.encode()).hexdigest():source for _,source in candidates}
        proposal=next((r for r in reports if r.get('pointer_return',{}).get('status')=='proposed'),None)
        proposals.append({'function':fn,'proposed':proposal is not None,
                          'declines':[r for r in reports if r.get('stage')=='pointer-return' and r['status']=='declined']})
        parent_source,parent_id=None,None
        for arm,label in [('baseline','binary-types:valid:1'),('pointer-return','binary-types:pointer-return:valid')]:
            report=next((r for r in reports if r.get('label')==label and r['status'] in ('generated','duplicate')),None)
            if report is None: continue
            source=by_hash[report['source_sha256']]; (ws/(arm+'.c')).write_text(source)
            compiler=NativeCompiler(repo,{'function':fn,'compile_target':targets[fn],
                'target_object':f'targets/{fn}/target.o','context':'empty-context'},output,
                output/'compiles'/fn/arm,budget=1,identity=identity)
            result=compiler(source,arm,parent_source=parent_source); measured=compiler.rows[-1]
            att=workspace.Attempt(result.compiled,0,result.exact,result.diff or '',measured.get('error') or '',
                '',verification=measured.get('verification'),frontend=measured.get('frontend'),compiler_recipe=compiler.recipe)
            attempt_id=workspace.record_attempt(conn,fn,source,att,strategy='m2c-pointer-return:'+arm,
                run_id=output.name,run_kind='dev-spike',parent_attempt_id=parent_id,
                wall_ms=int(measured['seconds']*1000),extra={'training_eligible':False,'generation':report,'score_available':False})
            row={'function':fn,'arm':arm,'compiled':result.compiled,'frontend_passed':(measured.get('frontend') or {}).get('passed') is True,
                 'exact':result.exact,'attempt_id':attempt_id,'source_sha256':measured['source_sha256'],
                 'target_sha256':measured['target_sha256'],'receipt':str(compiler.output/measured['artifact']/'receipt.json')}
            rows.append(row)
            if arm=='baseline': parent_source,parent_id=source,attempt_id
            write(output/'comparison.partial.json',{'rows':rows,'proposals':proposals})
            print(json.dumps(row),flush=True)
    conn.close()
    result={'rows':rows,'proposals':proposals,'attempts':len(rows),'exact':[r['function'] for r in rows if r['exact']],
            'model_calls':0,'deployed':False}
    write(output/'comparison.json',result); print(json.dumps({'attempts':len(rows),'exact':result['exact']}),flush=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--repo',type=Path,default=Path('/home/grant/decomp/sbk1'))
    ap.add_argument('--parent',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args(); run(a.repo,a.parent,a.output)

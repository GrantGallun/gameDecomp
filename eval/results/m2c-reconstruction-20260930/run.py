"""Isolated DEV comparison of m2c lifting choices; never production wiring."""
from pathlib import Path
import argparse
import contextlib
import importlib.util
import io
import json
import hashlib
import re
import shutil
import sqlite3
import sys
from dataclasses import asdict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(HERE))
from byte_address import ByteAddresses
from solver import binary_type_context as bc, binary_type_draft as bd
from solver import m2c_byte_view, m2c_input, repair_context, workspace, signals, mips_differential as diff
from eval.research_suite.compiler import NativeCompiler, environment
from m2c import main

PRIOR = ROOT/'eval/results/joint-reconstruction-20260930'
ARMS = [('baseline',False,()),('byte',True,()),
        ('byte-no-andor',True,('--no-andor',)),('byte-gotos',True,('--gotos-only',))]

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2)+'\n')

def prior_module():
    spec = importlib.util.spec_from_file_location('prior_probe',PRIOR/'probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def select(repo,output):
    prior = prior_module()
    census = json.loads((PRIOR/'census.json').read_text())
    dev,sealed = prior.partitions()
    fixed = json.loads((PRIOR/'selection.json').read_text())['functions']
    # Resolve leaf disassembly before any generation/compile outcomes are seen.
    # Sorted size/name sample of eight other DEV integer leaves with shifted adds.
    from solver import target_intake
    selected,raw = list(fixed),{}
    for fn in fixed:
        raw[fn] = (PRIOR/'portable/targets'/fn/'target.s').read_text()
    declined = []
    for fn in sorted(dev-set(fixed),key=lambda n:(census['metadata'].get(n,{}).get('insn_count',10**8),n)):
        row = census['metadata'].get(fn,{})
        if not 12 <= row.get('insn_count',0) <= 160:
            continue
        try:
            resolution = target_intake.resolve(repo,fn)
            if resolution.kind != 'disassembly':
                continue
            assembly = resolution.path.read_text()
            if (re.search(r'\b(?:jal|jalr|lwc1|swc1|ldc1|sdc1|mtc1|mfc1)\b',assembly)
                    or not re.search(r'\bsll\b',assembly) or not re.search(r'\baddu\b',assembly)
                    or not re.search(r'\b(?:lw|lhu|lbu|lh|lb)\b',assembly)):
                continue
            selected.append(fn)
            raw[fn] = assembly
            if len(selected) == 12:
                break
        except (OSError,ValueError,RuntimeError) as exc:
            declined.append({'function':fn,'error':str(exc)})
    assert not set(selected)&sealed
    write(output/'selection.json',{'functions':selected,'motivating':fixed,
        'transfer':selected[len(fixed):], 'heldout_overlap':[],
        'assembly_sha256':{fn:sha(a.encode()) for fn,a in raw.items()},
        'policy':'four prior sprites plus first eight size/name-ranked DEV integer leaves with shifted addition and loads; selected before outcome',
        'declined':declined})
    return selected,census,raw

def generation(repo,fn,assembly,ctx,folder,byte,flags):
    folder.mkdir(parents=True)
    assembly,aliases = m2c_input.normalize_o32_registers(assembly)
    (folder/'input.s').write_text(assembly)
    preprocessed,meta = bd._preprocess(repo,bd.CLEAN_PRELUDE+ctx['declarations'],folder)
    (folder/'context.c').write_text(preprocessed)
    stdout = io.StringIO()
    argv = ['--target','mips-ido-c','--no-cache','--valid-syntax','--context',str(folder/'context.c'),*flags,str(folder/'input.s')]
    try:
        from provenance import ProvenanceCollector
        collector = ProvenanceCollector()
    except ImportError:
        collector = contextlib.nullcontext()
    with collector,contextlib.redirect_stdout(stdout),ByteAddresses(enabled=byte) as adapter:
        rc = main.run(main.parse_flags(argv))
    text = stdout.getvalue()
    (folder/'m2c.stdout').write_text(text)
    if hasattr(collector,'report'):
        write(folder/'provenance.json',collector.report())
    write(folder/'generation.json',{'returncode':rc,'byte_address_changes':adapter.changes,
        'late_pointer_store_views':adapter.store_views,
        'context':meta,'argv':argv,'input_sha256':sha(assembly.encode()),'source_independent':True})
    if rc:
        return None,{'status':'m2c-failed','returncode':rc}
    lowered = m2c_byte_view.lower(text,fn,target_assembly=assembly)
    text = lowered['source']
    definition,end = repair_context.definition(text,fn)
    header = ctx['declarations'].replace(ctx['own_prototype']+'\n','',1)
    source = bd.CLEAN_PRELUDE+header+'\n'+text[definition.start():end]+'\n'
    (folder/'source.c').write_text(source)
    return source,{'status':'generated','byte_address_changes':adapter.changes,
        'late_pointer_store_views':adapter.store_views,
        'source_sha256':sha(source.encode()),'post_lowering':{k:v for k,v in lowered.items() if k!='source'}}

def semantic(fn,assembly,candidate,source,ctx):
    # Finite synthetic execution, target supplies all expected observations.
    # No authenticated game-domain or callee behavior assumptions are added.
    if re.search(r'\b(?:jal|jalr)\b',assembly):
        return {'status':'not-run','reason':'callee execution/arity environment not specified'}
    target = diff.Program.parse(fn,assembly)
    child = diff.Program.parse(fn,candidate)
    ptr_slots = {int(x) for x in re.findall(r'\*\s*arg([0-3])\b',ctx['own_prototype'])}
    returns = () if re.search(r'\bvoid\s+'+fn+r'\s*\(',source) else ('v0',)
    outcomes=[]
    for index,value in enumerate((0,1,2,7,127,255)):
        registers = tuple(('a'+str(slot),diff.PLAYER_BASE+slot*0x400 if slot in ptr_slots else value) for slot in range(4))
        case = diff.TestCase('synthetic-'+str(value),0xC000+index,entry_registers=registers)
        measured = diff.compare_programs(target,child,case,return_registers=returns,max_steps=2000)
        outcomes.append({'case':asdict(case),'status':measured.status,'reasons':measured.reasons,
                         'target_status':measured.target.status,'target_error':measured.target.error,
                         'candidate_status':measured.candidate.status})
    return {'status':'finite-synthetic','outcomes':outcomes,'passed':sum(r['status']=='passed' for r in outcomes),
            'failed':sum(r['status']=='failed' for r in outcomes),
            'inconclusive':sum(r['status']=='inconclusive' for r in outcomes),
            'debt':['finite synthetic memory/inputs; not game recordings or universal equivalence',
                    'observed source-independent prototype is a hypothesis, not proven entry-domain authority']}

def run(repo,output):
    output.mkdir(parents=True,exist_ok=False)
    write(output/'preregistration.json',{'kind':'m2c-lifting-dev-spike','arms':[x[0] for x in ARMS],
        'budget':48,'per_function_per_arm':1,'selection_before_compiler_outcomes':True,
        'compiler_choice':'retain baseline; choose exact/frontend/finite disagreement/diff-line-count in that order; no adaptive resampling',
        'training_eligible':False,'deployed':False,'code':{p.name:sha(p.read_bytes()) for p in HERE.glob('*.py')}})
    fns,census,assemblies = select(repo,output)
    model = bc.load(bc.find_elf(repo),output/'binary-cache')
    targets = {}
    for fn in fns:
        value = census['metadata'][fn]['compile_target']
        targets[fn] = value if value.startswith('build/') else 'build/'+value.replace('.c','.o')
        dest = output/'targets'/fn
        dest.mkdir(parents=True)
        if fn in json.loads((PRIOR/'selection.json').read_text())['functions']:
            shutil.copytree(PRIOR/'portable/targets'/fn,dest,dirs_exist_ok=True)
        else:
            prior_module().assemble(repo,fn,dest)
    identity = environment(repo,list(targets.values()))
    write(output/'environment.json',identity)
    (output/'empty-context').mkdir()
    conn = sqlite3.connect(output/'attempts.sqlite')
    conn.executescript((ROOT/'kb/schema.sql').read_text())
    for fn in fns:
        row = census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)',(row['tu_id'],targets[fn]))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)',(row['addr'],fn,row['tu_id'],row['insn_count']))
    conn.commit()
    rows=[]
    for fn in fns:
        # Use identical context per arm; motivating cases reproduce prior joint context.
        saved = PRIOR/'portable/drafts'/fn/'joint/valid/context.json'
        ctx = json.loads(saved.read_text()) if saved.exists() else model.context(fn,assemblies[fn])
        parent,parent_id = None,None
        for arm,byte,flags in ARMS:
            folder = output/'drafts'/fn/arm
            source,gen = generation(repo,fn,assemblies[fn],ctx,folder,byte,flags)
            row = {'function':fn,'arm':arm,'generation':gen,'compiled':False,'frontend_passed':False,'exact':False}
            if source is not None:
                compiler = NativeCompiler(repo,{'function':fn,'compile_target':targets[fn],
                    'target_object':f'targets/{fn}/target.o','context':'empty-context'},output,output/'compiles'/fn/arm,budget=1,identity=identity)
                result = compiler(source,arm,parent_source=parent)
                measured = compiler.rows[-1]
                row.update(compiled=result.compiled,frontend_passed=(measured.get('frontend') or {}).get('passed') is True,
                    exact=result.exact,source_sha256=measured['source_sha256'],error=measured.get('error'),
                    receipt=str(compiler.output/measured['artifact']/'receipt.json'))
                att = workspace.Attempt(result.compiled,0,result.exact,result.diff or '',measured.get('error') or '',
                    '',verification=measured.get('verification'),frontend=measured.get('frontend'),compiler_recipe=compiler.recipe)
                row['attempt_id'] = workspace.record_attempt(conn,fn,source,att,strategy='m2c-lifting-dev-spike:'+arm,
                    run_id=output.name,run_kind='dev-spike',parent_attempt_id=parent_id,
                    wall_ms=int(measured['seconds']*1000),extra={'generation':gen,'training_eligible':False,'score_available':False})
                if arm == 'baseline':
                    parent,parent_id = source,row['attempt_id']
                if result.compiled:
                    row['faults'] = asdict(signals.analyse(result.diff,0))
                    try:
                        row['semantic'] = semantic(fn,compiler.target_dump,result.dump,source,ctx)
                    except (OSError,ValueError,RuntimeError) as exc:
                        row['semantic'] = {'status':'unavailable','error':str(exc)}
            rows.append(row)
            write(output/'comparison.partial.json',{'rows':rows})
            print(json.dumps({k:row.get(k) for k in ('function','arm','compiled','frontend_passed','exact')}),flush=True)
    chosen=[]
    def rank(r):
        sem = r.get('semantic',{})
        return (not r['exact'],not (r['compiled'] and r['frontend_passed']),sem.get('failed',0),
                r.get('faults',{}).get('diff_lines',10**8))
    for fn in fns:
        group=[r for r in rows if r['function']==fn]
        best=min(group,key=rank)
        chosen.append({'function':fn,'arm':best['arm'],'rank':rank(best),'exact':best['exact'],
                       'frontend_passed':best['frontend_passed'],
                       'scope':'local compiler feedback selection; not semantic proof or heldout policy evaluation'})
    summary={arm:{key:sorted({r['function'] for r in rows if r['arm']==arm and r[key]}) for key in ('compiled','frontend_passed','exact')} for arm,_,_ in ARMS}
    write(output/'comparison.json',{'rows':rows,'summary':summary,'chosen':chosen,'deployed':False,'model_calls':0})
    conn.close()
    print(json.dumps(summary),flush=True)

if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--repo',type=Path,default=Path('/home/grant/decomp/sbk1'))
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    run(args.repo,args.output)

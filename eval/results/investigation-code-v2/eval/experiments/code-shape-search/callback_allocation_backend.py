"""Read-only IDO diagnostic discovery; all artifacts remain in this experiment."""
import json,re,subprocess,sys,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'eval/results/callback-allocation-backend-v1'
OUT.mkdir(exist_ok=True)
BIN=Path('/home/grant/decomp/sbk1/tools/ido-recomp/linux')
for name in ('cc','uopt','ugen'):
    p=subprocess.run(['strings',str(BIN/name)],capture_output=True,text=True)
    selected=[line for line in p.stdout.splitlines() if re.search(r'dump|liv[e]|alloc|color|regis|listing|usage|trace|keep|ucode|uopt|^-',line,re.I)]
    (OUT/f'{name}-strings.txt').write_text('\n'.join(selected))
    data=(BIN/name).read_bytes()
    swapped=b''.join(data[i:i+4][::-1] for i in range(0,len(data),4))
    strings=[m.group().decode('ascii') for m in re.finditer(rb'[ -~]{4,}',swapped)]
    selected=[line for line in strings if re.search(r'dump|liv[e]|alloc|color|regis|listing|usage|trace|keep|ucode|uopt|^-',line,re.I)]
    (OUT/f'{name}-swapped-strings.txt').write_text('\n'.join(selected))
    print(name,'SWAPPED',len(selected),'matching strings')

if '--compile' in sys.argv:
    source=ROOT/'eval/results/callback-inverse-final/createCallbackTaskPreservingArgs.c'
    (OUT/'baseline.c').write_text(source.read_text())
    command=[str(BIN/'cc'),'-c','-O2','-mips1','-G','0','-non_shared','-fullwarn','-Xcpluscomm','-nostdinc','-Wab,-r4300_mul','-woff','649,838,712,516','-I/home/grant/decomp/sbk1/include','-DLANGUAGE_C','-D_LANGUAGE_C','-D_MIPS_SZLONG=32','-DNDEBUG','-keep','-v','-S','baseline.c']
    result=subprocess.run(command,cwd=OUT,capture_output=True,text=True,timeout=120)
    (OUT/'diagnostic-command.json').write_text(json.dumps({'command':command,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr},indent=2))
    print(result.returncode,result.stdout,result.stderr)

if '--stages' in sys.argv:
    receipt=json.loads((OUT/'diagnostic-command.json').read_text())
    line=next(line for line in receipt['stderr'].splitlines() if line.startswith('/usr/lib/cfe '))
    command=shlex.split(line.split(' > ')[0]);command[0]=str(BIN/'cfe')
    command=[('-XS'+str(OUT/'baseline.T')) if arg.startswith('-XS') else arg for arg in command]
    cfe=subprocess.run(command,cwd=OUT,capture_output=True,timeout=120)
    (OUT/'baseline.B').write_bytes(cfe.stdout);(OUT/'cfe.log').write_bytes(cfe.stderr)
    print('cfe',cfe.returncode,len(cfe.stdout))
    for debug in ('plain','-zaloc','-zdbug','-d'):
        output='baseline'+debug+'.O'
        command=[str(BIN/'uopt'),'-v','-G','0','-EB','-g0','-O2','baseline.B',output,'-t','baseline.T','baseline'+debug+'.T']
        if debug!='plain':command.append(debug)
        p=subprocess.run(command,cwd=OUT,capture_output=True,timeout=120)
        (OUT/f'uopt{debug}.log').write_bytes(p.stdout+p.stderr)
        print(debug,p.returncode,(p.stdout+p.stderr).decode(errors='replace')[:1000])
    command=[str(BIN/'ugen'),'-v','-G','0','-EB','-g0','-O2','baselineplain.O','-o','diagnostic.G','-l','diagnostic.s','-t','baseline.T','-temp','diagnostic.temp','-d','-e','ugen.dump']
    p=subprocess.run(command,cwd=OUT,capture_output=True,timeout=120)
    (OUT/'ugen-dump.log').write_bytes(p.stdout+p.stderr)
    print('ugen',p.returncode,(p.stdout+p.stderr).decode(errors='replace')[:1000])

if '--allocation' in sys.argv:
    import hashlib
    for level in (5,6):
        command=[str(BIN/'uopt'),'-v','-G','0','-EB','-g0','-O2','baseline.B',f'allocation{level}.O','-t','baseline.T',f'allocation{level}.T',f'-zdbug:{level}']
        p=subprocess.run(command,cwd=OUT,capture_output=True,timeout=120)
        (OUT/f'alloc{level}.log').write_bytes(p.stdout+p.stderr)
        trace=OUT/'uoptlist'
        if trace.exists():trace.rename(OUT/f'uopt-level{level}.txt')
        print(level,p.returncode,(p.stdout+p.stderr).decode(errors='replace')[:300])
        output=OUT/f'allocation{level}.O'
        if output.exists():print('Ucode matches nondiagnostic',output.read_bytes()==(OUT/'baselineplain.O').read_bytes())

if '--verify' in sys.argv:
    import sqlite3,time,hashlib
    sys.path.insert(0,str(ROOT))
    from solver import workspace,byte_certificate
    repo=Path('/home/grant/decomp/sbk1');function='createCallbackTaskPreservingArgs'
    ws=workspace.bootstrap(repo,function)
    conn=sqlite3.connect(ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',timeout=120)
    tag=function+'_backend_verify_'+str(time.time_ns())
    attempt=workspace.score(ws,repo,tag,(OUT/'baseline.c').read_text(),conn=conn,func=function,strategy='callback-backend-verification',model='',action='verify diagnostic assembly against production object',run_kind='callback-backend-verification')
    command=json.loads((OUT/'diagnostic-command.json').read_text())['command']
    command=[arg for arg in command if arg not in ('-S','-v')]+['-Wc,-d','-o','diagnostic.o']
    p=subprocess.run(command,cwd=OUT,capture_output=True,text=True)
    (OUT/'native-diagnostic.log').write_text(p.stdout+p.stderr)
    summary={'production_score':attempt.score,'production_attempt':attempt.receipt_id,'assembler_returncode':p.returncode,'assembler_stderr':p.stderr,'diagnostic_s_matches_plain_s':(OUT/'diagnostic.s').read_bytes()==(OUT/'baseline.s').read_bytes(),'allocation6_O_matches_plain_O':(OUT/'allocation6.O').read_bytes()==(OUT/'baselineplain.O').read_bytes()}
    if p.returncode==0:
        original=byte_certificate.object_image((ws/f'{tag}.o').read_bytes())
        diagnostic=byte_certificate.object_image((OUT/'diagnostic.o').read_bytes())
        summary['production_object_image']=original
        summary['diagnostic_object_image']=diagnostic
        summary['images_equal']=original==diagnostic
    (OUT/'verification.json').write_text(json.dumps(summary,indent=2))
    print({k:v for k,v in summary.items() if not k.endswith('_image') and k!='assembler_stderr'})

if '--scratch' in sys.argv:
    import sqlite3,time
    from dataclasses import asdict
    sys.path.insert(0,str(ROOT))
    from solver import workspace
    from eval import semantic_stress_pilot as stress
    repo=Path('/home/grant/decomp/sbk1');function='createCallbackTaskPreservingArgs'
    out=ROOT/'eval/results/callback-allocation-backend-v2';out.mkdir(exist_ok=True)
    source=(ROOT/'eval/results/callback-allocation-selector-v3/00.c').read_text()
    expressions=['(u16)(s16)type','(u16)(s32)(s16)type','(u16)(u32)(s16)type','(u16)(u64)type','(u16)(s64)type','(u16)~(~type)','(u16)(-(-type))','(u16)(type ^ 0)','(u16)((type ^ 0x8000) ^ 0x8000)','(u16)((type << 16) >> 16)','(u16)((type & 0xFFFF) + 0)','(u16)(type | 0x10000)','(u16)(type + 0x10000)','(u16)(type - 0x10000)','(u16)(type * 65537U)','(u16)((type << 1) >> 1)','(u16)(type / 1)','(u16)(type * 1)','(u16)(type % 65536U)','(u16)((s32)(type << 16) >> 16)','(u16)(type & (s16)-1)','(u16)((u16)(type+1)-1)','(u16)((type-1)+1)','(u16)(type | (type & 0xFFFF))']
    ws=workspace.bootstrap(repo,function);db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
    conn=sqlite3.connect(db,timeout=120);rows=[]
    for i,expression in enumerate(expressions):
        code=source.replace('type = (u16)type;',f'type = {expression};')
        tag=function+'_scratch_'+str(time.time_ns())
        a=workspace.score(ws,repo,tag,code,conn=conn,func=function,strategy='callback-scratch-cursor',model='',action=expression,run_kind='callback-scratch-cursor')
        (out/f'{i:02d}.c').write_text(code)
        rows.append({'expression':expression,'attempt':asdict(a)})
        (out/'scores.json').write_text(json.dumps(rows,indent=2))
        print(i,expression,a.score,a.exact,flush=True)
    conn.close()
    chosen=sorted(range(len(rows)),key=lambda i:rows[i]['attempt']['score'],reverse=True)
    chosen=[out/f'{i:02d}.c' for i in chosen[:3] if rows[i]['attempt']['score']>98.511]
    if chosen:
        cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
        r=stress.run(repo=repo,db=db,census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function=function,candidate_paths=tuple(chosen),panel_cases=stress._cases_from_rows(cases))
        for c in r['candidates']:print('REPLAY',c['candidate_path'],c['attempt']['score'],c['differential']['all'],flush=True)

if '--replay-closest' in sys.argv:
    sys.path.insert(0,str(ROOT))
    from eval import semantic_stress_pilot as stress
    out=ROOT/'eval/results/callback-allocation-backend-v2'
    cases=json.loads((ROOT/'eval/results/last-push-callback-final/createCallbackTaskPreservingArgs.cases.json').read_text())
    r=stress.run(repo=Path('/home/grant/decomp/sbk1'),db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'closest-replay.json',function='createCallbackTaskPreservingArgs',candidate_paths=(out/'00.c',),panel_cases=stress._cases_from_rows(cases))
    c=r['candidates'][0];print('REPLAY',c['attempt']['score'],c['differential']['all'])

if '--closest-stages' in sys.argv:
    out=ROOT/'eval/results/callback-allocation-backend-v2'
    receipt=json.loads((OUT/'diagnostic-command.json').read_text())
    line=next(line for line in receipt['stderr'].splitlines() if line.startswith('/usr/lib/cfe '))
    command=shlex.split(line.split(' > ')[0]);command[0]=str(BIN/'cfe')
    command=[('-XS'+str(out/'closest.T')) if arg.startswith('-XS') else ('00.c' if arg=='baseline.c' else arg) for arg in command]
    cfe=subprocess.run(command,cwd=out,capture_output=True,timeout=120)
    (out/'closest.B').write_bytes(cfe.stdout)
    command=[str(BIN/'uopt'),'-G','0','-EB','-g0','-O2','closest.B','closest.O','-t','closest.T','closest.optimized.T']
    p=subprocess.run(command,cwd=out,capture_output=True,timeout=120)
    command=[str(BIN/'ugen'),'-G','0','-EB','-g0','-O2','closest.O','-o','closest.G','-l','closest.s','-t','closest.T','-temp','closest.temp','-d','-e','closest.dump']
    p=subprocess.run(command,cwd=out,capture_output=True,timeout=120)
    (out/'closest-ugen.log').write_bytes(p.stdout+p.stderr)
    print('closest stages',cfe.returncode,p.returncode)
    rows=json.loads((out/'scores.json').read_text())
    (out/'closest.diff').write_text(rows[0]['attempt']['diff'])

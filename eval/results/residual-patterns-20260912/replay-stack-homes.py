"""Bounded, private compiler probes for repeated stack-home residuals."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from eval import reconstruct, campaign_workers, semantic_lane
from solver import workspace, principle_variants, modelrepair, refine

out = Path('eval/results/residual-patterns-20260912/stack-home-v1')
native = Path('/home/grant/decomp/residual-stack-home-v1-20260912')
if out.exists() or native.exists():
    raise ValueError('fresh experiment paths required')
out.mkdir()
native.mkdir()
db = native/'history.sqlite'
reconstruct.copy_history(Path('/home/grant/decomp/partial-popup-v5-20260912/history.sqlite'), db)
names = {'startEndingSlashRepeatAnim', 'updateEndingJamPhase3DPrep', 'updateEndingJamPhase3FAnim3'}
records = json.loads((out.parent/'current-v1/records.json').read_text())
report = {'private_db':str(db), 'scope':'Compiler experiments only; no canonical import or model calls', 'functions':[]}
with sqlite3.connect(db) as conn:
    refine.ensure_schema(conn)
    for record in records:
        name = record['name']
        if name not in names:
            continue
        source = conn.execute('SELECT source_code FROM attempts WHERE id=?', (record['attempt_id'],)).fetchone()[0]
        assert hashlib.sha256(source.encode()).hexdigest() == record['source_sha256']
        repo = campaign_workers.isolate(Path('/home/grant/decomp/sbk1'), native/name, name)
        ws = repo/'nonmatchings'/name
        local = re.search(r'\bs32\s+(sp[0-9A-Fa-f]+)\s*;', source)
        if not local:
            raise ValueError('expected selected scalar local')
        variants = [('baseline',source)] + [(v.label,v.source) for v in principle_variants.isolated_register_web(source,name,max_variants=2)]
        variants += [('pad4-before',source[:local.start()]+'volatile u8 gd_stack_pad[4];\n    '+source[local.start():]),
                     ('pad4-after',source[:local.end()]+'\n    volatile u8 gd_stack_pad[4];'+source[local.end():])]
        union = source[:local.start()]+'union { s32 value; double align; } gd_stack_slot;'+source[local.end():]
        union = re.sub(r'\b'+local[1]+r'\b','gd_stack_slot.value',union)
        variants += [('aligned-union-local',union)]
        row = {'name':name,'selected_attempt':record['attempt_id'],'source_sha256':record['source_sha256'],'attempts':[]}
        report['functions'].append(row)
        for index,(label,changed) in enumerate(variants):
            tag = f'{name}_stackprobe_{index}_{time.time_ns()}'
            att = workspace.score(ws,repo,tag,changed,conn=conn,func=name,
                run_id='residual-stack-home-20260912',run_kind='residual-stack-home-probe',
                run_config={'motif':'same operations, target local home0x18 versus candidate0x1c', 'canonical_import':False},
                parent_attempt_id=record['attempt_id'],relation='residual-stack-home-probe',strategy=label,
                extra={'source_hash_verified':True,'authority':'unvalidated compiler experiment'})
            saved = {'label':label,'attempt_id':att.receipt_id,'compiled':att.compiled,'score':att.score,
                'exact':att.exact,'frontend':att.frontend,'stderr':att.compiler_stderr,'diff':att.diff,
                'source':changed,'object':str(ws/(tag+'.o'))}
            row['attempts'].append(saved)
            if workspace.repair_complete(att):
                panel = semantic_lane.Panel(repo,ws,name,64,10000,128,header_source=changed)
                saved['semantic'] = panel(modelrepair.CandidateState(changed,att,ws/(tag+'.o')))
            (out/'report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'name':name, **{k:saved[k] for k in ('label','attempt_id','compiled','score','exact')},
                              'frontend_passed':(att.frontend or {}).get('passed')}),flush=True)

"""Replay the shipped generator and measure its reach in the selected motif."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from eval import semantic_lane, residual_patterns
from solver import rewrites, workspace, modelrepair

root = Path('eval/results/residual-patterns-20260912')
native = Path('/home/grant/decomp/residual-stack-home-v1-20260912')
records = json.loads((root/'current-v1/records.json').read_text())
names = {'startEndingSlashRepeatAnim','updateEndingJamPhase3DPrep','updateEndingJamPhase3FAnim3'}
pattern = 'lw R0,0x18(sp) => lw R0,0x1c(sp)'
report = {'pattern':pattern,'private_db':str(native/'history.sqlite'),'applicability':[],'replays':[]}
with sqlite3.connect(native/'history.sqlite') as conn:
    for record in records:
        blocks,_ = residual_patterns.changes(record['diff'])
        if not any(residual_patterns.signature(b['target'],b['candidate']) == pattern for b in blocks):
            continue
        selected = conn.execute('SELECT source_code FROM attempts WHERE id=?',(record['attempt_id'],)).fetchone()
        if not selected:
            report['applicability'].append({'name':record['name'],'attempt_id':record['attempt_id'],'status':'absent_from_private_history'})
            continue
        source = selected[0]
        assert hashlib.sha256(source.encode()).hexdigest() == record['source_sha256']
        candidates = rewrites.stack_home_padding_rewrites(source,record['diff'])
        report['applicability'].append({'name':record['name'],'attempt_id':record['attempt_id'],'candidates':len(candidates)})
        if record['name'] not in names:
            continue
        candidates = [r for r in rewrites.propose(source,record['diff']) if r.kind == 'stack-home']
        assert len(candidates) == 1
        changed = candidates[0](source)
        name = record['name']
        repo = native/name
        ws = repo/'nonmatchings'/name
        tag = name+'_final_stack_generator_'+str(time.time_ns())
        att = workspace.score(ws,repo,tag,changed,conn=conn,func=name,run_id='residual-stack-home-final-generator-20260912',
            run_kind='residual-stack-home-generator-verification',run_config={'pattern':pattern,'model_calls':0,'canonical_import':False},
            parent_attempt_id=record['attempt_id'],relation='stack-home-generator',strategy=candidates[0].label,
            extra={'catalog_pattern':'ido-single-local-stack-home-padding','source_sha256':record['source_sha256']})
        panel = semantic_lane.Panel(repo,ws,name,64,10000,128,header_source=changed)
        semantic = panel(modelrepair.CandidateState(changed,att,ws/(tag+'.o')))
        row = {'name':name,'parent_attempt_id':record['attempt_id'],'attempt_id':att.receipt_id,'score':att.score,
            'exact':att.exact,'compiled':att.compiled,'frontend':att.frontend,'semantic':semantic,
            'object':str(ws/(tag+'.o')),'source':changed,'diff':att.diff,'label':candidates[0].label}
        report['replays'].append(row)
        print(json.dumps({k:row[k] for k in ('name','attempt_id','score','exact','label')}),flush=True)
    report['counts'] = {'motif_functions':len(report['applicability']),
        'source_bound':sum('candidates' in r for r in report['applicability']),
        'generator_fires':sum(bool(r.get('candidates')) for r in report['applicability'])}
(root/'stack-home-v1/final-generator.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report['counts']))

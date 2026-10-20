"""Targeted valid-list diagnostic controls beyond the synthetic default panel."""
import hashlib
import json
from pathlib import Path
from dataclasses import asdict
from eval import agentrepair, semantic_lane
from solver import mips_differential as d, workspace


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    output=root/'eval/results/v3-record-voice-paths-v1.json'
    if output.exists(): raise ValueError('refusing overwrite')
    receipt=root/'eval/results/v3-record-voice-replay-v2.json'
    result=json.loads(receipt.read_text())['result']
    source=Path(result['best_source_path']).read_text()
    if hashlib.sha256(source.encode()).hexdigest()!=result['best_source_sha256']:
        raise ValueError('source mismatch')
    ws=repo/'nonmatchings/_allocatePVoice'
    attempt=workspace.score(ws,repo,'record_paths_control',source)
    if not attempt.compiled or not attempt.frontend['passed']: raise ValueError('compile regression')
    panel=semantic_lane.Panel(repo,ws,'_allocatePVoice',64,10000,5000,None,source)
    obj=ws/'record_paths_control.o'
    assembly=workspace.semantic_assembly(obj.with_name(obj.stem+'_object_dump_normalized.s').read_text(),obj)
    cases=[]
    for path in ('empty','free','lame','active_accept','active_priority_reject','active_busy','active_two'):
        node=0x10000100
        head14=node if path=='free' else 0
        head4=node if path=='lame' else 0
        headC=node if path.startswith('active') else 0
        writes=[(4,4,head4),(12,4,headC),(20,4,head14),
                (0x100,4,0),(0x104,4,0),(0x108,4,0x10000200),
                (0x1D8,4,1 if path=='active_busy' else 0),
                (0x216,2,20 if path=='active_priority_reject' else 3)]
        if path=='active_two':
            writes += [(0x100,4,0x10000300),(0x300,4,0),(0x304,4,node),
                       (0x308,4,0x10000400),(0x3D8,4,0),(0x416,2,2)]
        cases.append(d.TestCase(path,54784,player_writes=tuple(writes),
            entry_registers=(('a0',0x10000000),('a1',0x11000000),('a2',10))))
    rows=d.run_suite(panel.target,assembly,tuple(cases),target_name='_allocatePVoice',
        call_arities=panel.arities,return_registers=panel.returns,max_steps=10000,
        callee_environment=panel.callee_environment)
    report={'source_sha256':result['best_source_sha256'],'parent_receipt':str(receipt),
        'parent_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),
        'scope':'hand-constructed valid-list diagnostic controls; not automatic input generation or unseen transfer',
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False,
        'inputs':[asdict(case) for case in cases],
        'cases':[{'case':row.case,'status':row.status,'reasons':row.reasons,
                  'target_status':row.target.status,'candidate_status':row.candidate.status,
                  'feedback':d.causal_feedback(row,max_steps=5) if row.status!='passed' else ''} for row in rows],
        'target_coverage':d.coverage_report(d.Program.parse('_allocatePVoice',panel.target),[r.target for r in rows]).to_dict()}
    agentrepair._atomic_json(output,report)
    print(json.dumps(report))


if __name__=='__main__': main()

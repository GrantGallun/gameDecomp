import json, sys
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval import differential_repair_pilot as pilot, semantic_stress_pilot as stress
from solver.mips_differential import TestCase, PLAYER_BASE

out=ROOT/'eval/results/swarm-timer-final'
out.mkdir(exist_ok=True)
cases=list(stress._cases_from_rows(json.loads((ROOT/'eval/results/last-push-timer-v3/calculateRaceTimerDelta.cases.json').read_text())))
values=[(0,0,0),(0,0,255),(0,0,256),(0,0,32767),(0,0,32768),(0,0,65535),(0,59,0),(1,0,0),(98,59,65535),(99,0,0),(127,127,65535),(128,128,32768),(255,255,65535)]
for i,left in enumerate(values):
    for j,right in enumerate(values):
        memory=tuple((offset+field,width,value) for offset,record in ((0,left),(16,right)) for field,width,value in ((0,1,record[0]),(1,1,record[1]),(2,2,record[2])))
        cases.append(TestCase(f'explicit-timer-{i}-{j}',88000+i*len(values)+j,memory,(),(('a1',PLAYER_BASE+16),('a2',PLAYER_BASE+32))))
(out/'cases.json').write_text(json.dumps([asdict(c) for c in cases],indent=2))
result=pilot.run(repo=Path('/home/grant/decomp/sbk1'),db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',source_path=ROOT/'eval/results/swarm-timer-v4/5.c',source_parent_attempt_id=31956,output=out/'replay.json',best_source_out=out/'calculateRaceTimerDelta.c',model='unused',endpoint='unused',rounds=0,timeout=120,think='high',num_thread=1,temperature=0,diagnosis_num_predict=1,patch_num_predict=1,patch_retries=0,compiler_retries=0,max_stalls=1,seed=88000,cache_dir=None,function='calculateRaceTimerDelta',cases=tuple(cases),call_arities={},return_registers=('v0',),deterministic_only=True,proposal_replay_budget=0)
print(json.dumps({k:result['result'][k] for k in ('semantic_cases_passed','all_semantic_cases_passed','exact')},indent=2))

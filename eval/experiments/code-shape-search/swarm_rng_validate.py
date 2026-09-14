import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval import differential_repair_pilot as pilot, semantic_stress_pilot as stress
out=ROOT/'eval/results/swarm-rng-validation'
out.mkdir(exist_ok=True)
cases=stress._cases_from_rows(json.loads((ROOT/'eval/results/last-push-rng-v3/__MusIntRandom.cases.json').read_text()))
r=pilot.run(repo=Path('/home/grant/decomp/sbk1'),db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',
    source_path=ROOT/'eval/results/swarm-rng-loops/3.c',source_parent_attempt_id=31825,
    output=out/'receipt.json',best_source_out=out/'__MusIntRandom.c', model='unused',endpoint='unused',rounds=0,
    timeout=180,think='high',num_thread=4,temperature=0,diagnosis_num_predict=1,patch_num_predict=1,
    patch_retries=0,compiler_retries=0,max_stalls=1,seed=20260908,cache_dir=None,function='__MusIntRandom',
    cases=cases,call_arities={},return_registers=('v0',),deterministic_only=True,proposal_replay_budget=0)
print(json.dumps(r['result']['best_attempt']),r['result']['semantic_cases_passed'],r['result']['all_semantic_cases_passed'],flush=True)

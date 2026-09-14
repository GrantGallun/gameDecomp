import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from eval import semantic_stress_pilot as stress
out=ROOT/'eval/results/callback-inverse-structure-v1'
cases=json.loads((ROOT/'eval/results/swarm-integrated-callback/createCallbackTaskPreservingArgs.cases.json').read_text())
r=stress.run(repo=Path('/home/grant/decomp/sbk1'),db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',function='createCallbackTaskPreservingArgs',candidate_paths=(out/'07.c',),panel_cases=stress._cases_from_rows(cases))
for row in r['candidates']:print(row['candidate_path'],row['attempt']['score'],row['differential']['all'],flush=True)

import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval import semantic_stress_pilot as stress
out=ROOT/'eval/results/callback-finish-dispatch-v1'
rows=json.loads((ROOT/'eval/results/swarm-integrated-callback/createCallbackTaskPreservingArgs.cases.json').read_text())
r=stress.run(repo=Path('/home/grant/decomp/sbk1'),db=ROOT/'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite',
    census_path=ROOT/'eval/results/dag-pipeline-census-v32-project-defines.json',output=out/'replay.json',
    function='createCallbackTaskPreservingArgs',candidate_paths=(out/'25.c',),panel_cases=stress._cases_from_rows(rows))
for c in r['candidates']: print(c['attempt']['score'],c['differential']['all'],flush=True)

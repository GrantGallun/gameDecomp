"""Post-trial feedback correction; no additional compiles or deployment."""
import json
from pathlib import Path
import run as trial

here=Path(__file__).resolve().parent
original=json.loads((here/'portable/comparison.json').read_text())
corrected=[]
for row in original['rows']:
    row=dict(row); fn,arm=row['function'],row['arm']
    if row['compiled']:
        folder=here/'portable/compiles'/fn/arm
        ctx=json.loads((here/'portable/followup'/(fn+'-context.json')).read_text())
        row['semantic']=trial.semantic(fn,(folder/'target.normalized.s').read_text(),
            (folder/'attempt-00001/candidate_object_dump_normalized.s').read_text(),
            (folder/'attempt-00001/source.c').read_text(),ctx)
    corrected.append(row)
chosen=[]
for fn in dict.fromkeys(r['function'] for r in corrected):
    group=[r for r in corrected if r['function']==fn]
    baseline=next(r for r in group if r['arm']=='baseline')
    # Missing/inconclusive execution is never ranked as zero disagreement.
    eligible=[r for r in group if r['compiled'] and r['frontend_passed'] and
        r.get('semantic',{}).get('passed')==6 and
        r['semantic'].get('failed')==r['semantic'].get('inconclusive')==0]
    winner=min(eligible,key=lambda r:r['faults']['diff_lines']) if eligible else baseline
    chosen.append({'function':fn,'arm':winner['arm'],'complete_synthetic_cases':bool(eligible),
        'diff_lines':winner.get('faults',{}).get('diff_lines'),
        'policy':'all six bounded cases pass and frontend valid before comparing textual object diffs; otherwise retain baseline',
        'deployed':False,'exact':winner['exact']})
result={'kind':'post-trial-normalized-target-recheck','new_compiles':0,'original_preserved':True,
    'rows':corrected,'chosen':chosen,'model_calls':0,'deployed':False,
    'limits':['finite synthetic inputs; not proof','text diff size depends on register naming and instruction alignment',
        'original selector incorrectly treated unavailable execution as zero disagreement; it was never deployed']}
trial.write(here/'feedback.json',result)
print(json.dumps({'rows':len(corrected),'chosen':chosen}))

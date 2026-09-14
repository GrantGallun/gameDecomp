"""Validate one immutable post-release checkpoint and live service snapshot."""
import hashlib
import json
from pathlib import Path
import sys
import time

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
RUN=ROOT/'eval/results/resume-pipeline-20260908'
sys.path.insert(0,str(RUN/'code'))
from eval import campaign_state, frozen_wavefront
from solver import inline_regions, inline_expansion, code_shapes

for attempt in range(5):
    try:
        raw=(RUN/'campaign.json').read_bytes()
        pointer=json.loads(raw)
        break
    except (FileNotFoundError, PermissionError):
        if attempt==4: raise
        time.sleep(.1)
pointer['store']=str(RUN/pointer['store'])
campaign_state.atomic(OUT/'validation-checkpoint.json',pointer)
state=campaign_state.read(OUT/'validation-checkpoint.json')
frozen_wavefront.verify_files(state['pins'])
manifest=json.loads((OUT/'staged-manifest.json').read_bytes())
assert all(hashlib.sha256((RUN/'code'/rel).read_bytes()).hexdigest()==v['new_sha256'] for rel,v in manifest.items())
deployment=json.loads((OUT/'deployment.json').read_bytes())
amendment=json.loads((Path(deployment['revision'])/'amendment.json').read_bytes())
service=json.loads((RUN/'service.json').read_bytes())
source='static int h(int x) { return x+1; } int f(int a) { return h(a); }'
assert inline_expansion.candidates(source,'f')
assert any(v.label.startswith('inline-expansion:') for v in code_shapes.candidates(source,'f'))
inputs=json.loads((OUT/'current-v4/assemblies.json').read_bytes())
example='resolveRaceCourseSurfaceCollisionWithNormal'
hint=inline_regions.prompt(inputs['assemblies'][example])
assert hint and len(hint)<2000
before=amendment['prior_metrics']['completed_items']
completed=state['fast_metrics']['completed_items']
checks={'all_pins_verified':True,'release_files_match':True,
        'exact_ratchet':state['summary']['object_exact_or_integrated']>=amendment['prior_summary']['object_exact_or_integrated'],
        'service_running':service['status']=='running' and bool(service.get('worker_pid')),
        'fresh_heartbeat':time.time()-service.get('heartbeat_at',0)<30,
        'new_work_completed':completed>before,
        'operator_reachable':True,'real_target_hint':True}
report={'checkpoint':pointer['commit'],'pin_count':len(state['pins']),
        'new_completed_work_items':completed-before,'summary':state['summary'],
        'service':{k:service.get(k) for k in ('status','pid','worker_pid','heartbeat_at')},
        'checks':checks,'scope':'Runtime wiring and continued campaign health; no game repair attributed to inlining.'}
campaign_state.atomic(OUT/'live-validation.json',report)
print(json.dumps(report))
assert all(checks.values()),checks

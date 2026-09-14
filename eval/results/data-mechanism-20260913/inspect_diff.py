import difflib
from pathlib import Path
OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
for rel in ('eval/fast_campaign.py','eval/completion_campaign.py','eval/agentrepair.py','eval/semantic_lane.py',
            'solver/repair_queue.py','solver/principle_variants.py','solver/hardware_environment.py',
            'patterns/catalog.py','solver/function_boundary.py','eval/prepare_integration.py'):
    delta=''.join(difflib.unified_diff((LIVE/rel).read_text().splitlines(True),(ROOT/rel).read_text().splitlines(True),fromfile=rel,tofile=rel))
    (OUT/(Path(rel).stem+'.diff')).write_text(delta)
    print(rel,len(delta.splitlines()))

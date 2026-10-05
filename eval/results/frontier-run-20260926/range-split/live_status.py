import gc
import json
import sys
from pathlib import Path

sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from eval.campaign_state import read

path = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json')
state = read(path)
node = state['nodes']['drawControllerPakFileDeleteConfirmOptions']
print(json.dumps({key: node.get(key) for key in ('status', 'score', 'attempt_id', 'source_sha256', 'address')}, sort_keys=True))
del state, node
gc.collect()

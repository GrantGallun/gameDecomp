"""Fresh installed-profile and unchanged-node verification, without launching work."""
import hashlib
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
REVISION = ROOT / 'eval/results/resume-pipeline-20260908/revisions/20261004-compiler-localization'
FROZEN = REVISION.parents[1] / 'code'
sys.path.insert(0, str(FROZEN))
from eval import agentrepair, campaign_state, completion_campaign
from solver import modelrepair, repair_queue

manifest = json.loads((REVISION / 'stage.json').read_bytes())
amendment = json.loads((REVISION / 'amendment.json').read_bytes())
state_path = Path(manifest['state_path'])
pointer = json.loads(state_path.read_bytes())
state = campaign_state.read(state_path)
assert completion_campaign.digest(state['nodes']) == manifest['baseline_nodes_sha256']
assert pointer['summary']['object_exact_or_integrated'] == manifest['baseline_exact']
assert pointer['commit'] == amendment['result']['commit']
assert 'compiler_localization' in inspect.signature(agentrepair.run).parameters
assert 'compiler_localization' in inspect.signature(modelrepair.search).parameters
assert any(p.get('compiler_localization') for p in completion_campaign.PROFILES)
for rel, row in manifest['changed'].items():
    actual = hashlib.sha256((FROZEN / rel).read_bytes()).hexdigest()
    assert actual == row['new_sha256'] == state['pins'][str(FROZEN / rel)]
# Show that the additional repair can actually be selected, after existing visits.
available = []
for name, node in state['nodes'].items():
    if repair_queue.lane(node) != repair_queue.Lane.BYTE:
        continue
    key = repair_queue.evidence_key(node)
    copy = {**node, 'jobs': list(node.get('jobs', [])) + [
        {'profile': p['name'], 'evidence_key': key, 'source_sha256': node.get('source_sha256')}
        for p in completion_campaign.PROFILES if not p.get('compiler_localization')]}
    # In-memory projection only; preserve measured deterministic prerequisites.
    for _ in range(16):
        profile = repair_queue.next_profile(copy, state['config']['model_calls'], completion_campaign.PROFILES)
        if profile is None:
            break
        if profile.get('compiler_localization'):
            available.append(name)
            break
        copy['jobs'].append({'profile': profile['name'], 'evidence_key': key,
                             'source_sha256': copy.get('source_sha256')})
result = {'installed': True, 'commit': pointer['commit'], 'campaign_status': state['status'],
          'retained_nodes_unchanged': True, 'object_exact_or_integrated': manifest['baseline_exact'],
          'model_digest_unchanged': state['model_digest'] == manifest['model_digest'],
          'changed_files_verified': len(manifest['changed']),
          'additional_profile_reachable_functions': len(available), 'examples': available[:8]}
assert available
(Path(__file__).parent / 'installed-verification.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))

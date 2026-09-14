import copy
import json
from pathlib import Path
from eval import completion_campaign as campaign
from solver import repair_queue
out=Path(__file__).resolve().parent
records={label:json.loads(next(out.glob('replay-'+label+'-*/repair.json')).read_bytes()) for label in ('frozen','staged')}
staged=records['staged']
before={'status':'pending','jobs':[], 'source_sha256':staged['root']['source_sha256'],
        'residual':staged['root']['residual'],'semantic_validation':staged['result']['semantic_validation']}
profile=repair_queue.next_profile(before,3,campaign.PROFILES)
assert profile['name']=='local_rewrites'
projections={}
for label,record in records.items():
    result=record['result']
    node=copy.deepcopy(before)
    campaign.accept(node,profile,{**result,'source_sha256':result['best_source_sha256'],
                                 'residual':result['best_residual'],'attempt_id':result['best_attempt_id']},Path(label+'.json'))
    projections[label]={'before_evidence_key':profile['evidence_key'],'after_evidence_key':repair_queue.evidence_key(node),
                        'same_key':profile['evidence_key']==repair_queue.evidence_key(node),
                        'next_profile':repair_queue.next_profile(node,3,campaign.PROFILES)}
report={'scope':'Fresh measured root node, identical jobs/config, real paired repair receipts fed through production accept/next_profile; no live import',
        'projections':projections}
(out/'replay-queue-projection.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))

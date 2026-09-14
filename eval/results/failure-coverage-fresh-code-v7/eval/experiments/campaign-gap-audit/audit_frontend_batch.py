"""Source-bound engineering ownership for the exposed remaining-frontend batch."""
import argparse
import hashlib
import json
from pathlib import Path
from eval import agentrepair

ROUTES={
 'alResamplePull':(['compile_recovery','type_transaction','void_field_repair'],'Reconcile public function ABI and pointer-valued/typed record views together.'),
 'collidePlayerWithCourseTriggerVolume':(['frontend_repair','address_units'],'Bind typed call byte offsets and indexed absolute-address dereference to target evidence.'),
 'drawTrickAttackChallengeHud':(['stack_buffers','stack_object_repair'],'Recover one formatting buffer and endpoint aliases; do not declare independent dummy locals.'),
 'osMotorStop':(['stack_object_repair','m2c_byte_view'],'Reconstruct unknown SDK stack objects and unsupported m2c representations.'),
 'updateRaceResultsFlow':(['type_plan','compile_recovery'],'Recover unknown local pointer/aggregate views before expression normalization.'),
 '__osContRamRead':(['local_record_repair','m2c_byte_view'],'Recover incomplete SDK packet layout and unaligned accesses with explicit width/alignment evidence.'),
 'initCharacterSelectCourseMenuFromRace':(['compile_recovery','type_transaction'],'Repair malformed declaration/type-name binding using header evidence; preserve call and data ABI.'),
 'updateRacePlayerMode07SpiralExit':(['indexed_address_repair','compile_recovery'],'Recover indexed unknown global and call-site ABI intentionally omitted from shared header.'),
 'updateRaceSetupNamePlateSlideIn':(['compile_recovery','project_headers'],'Import actor type context and establish missing callback contracts without guessed shared prototypes.'),
 'updateRacePlayerGroundAlignment':(['stack_result_repair','aggregate_scalar_repair','globaldecl'],'Recover wide-call ABI, probe-table declaration and aggregate views together.'),
}


def audit(batch_path):
    batch=json.loads(batch_path.read_text())
    if batch.get('status')!='complete':raise ValueError('batch is not terminal complete')
    rows=[]
    for entry in batch['rows']:
        path=batch_path.parent/Path(entry['receipt']).name
        receipt=json.loads(path.read_text());result=receipt['result']
        source=path.with_suffix('.best.c').read_text()
        digest=hashlib.sha256(source.encode()).hexdigest()
        if digest!=result['best_source_sha256'] or digest!=entry['source_sha256']:
            raise ValueError('source identity mismatch')
        owners,next_action=ROUTES.get(entry['function'],([], 'Unclassified: inspect before routing.'))
        residual=result['best_residual']
        rows.append({'function':entry['function'],'receipt':path.name,
            'receipt_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source_sha256':digest,
            'compiled':residual['compiled'],'frontend_passed':residual.get('frontend',{}).get('passed'),
            'normalizations':result['normalization_candidates'],'owners':['solver/'+o+'.py' for o in owners],
            'next_action':next_action,'repair_status':'unresolved' if not residual.get('frontend',{}).get('passed') else 'frontend_passed',
            'diagnostics':residual.get('frontend',{}).get('diagnostics',''),
            'compiler_error':residual.get('compiler_error_signature','')})
    return {'kind':'frontend-engineering-audit','batch_sha256':hashlib.sha256(batch_path.read_bytes()).hexdigest(),
            'rows':rows,'unclassified':[r['function'] for r in rows if not r['owners']],
            'scope':'manual engineering routes bound to current receipts; classification is not a fix or fresh transfer'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():raise ValueError('refusing overwrite')
    report=audit(args.batch);agentrepair._atomic_json(args.out,report)
    print(json.dumps({'functions':len(report['rows']),'unclassified':report['unclassified']}))


if __name__=='__main__':main()

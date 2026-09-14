"""Audit final-source activation replay; reuse campaign symptom accounting.

No causal closure is inferred from this census. In particular, explicit assisted
callee/callback experiments are not silently counted as default activation.
"""
from collections import Counter
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3

from eval import agentrepair, frozen_wavefront

replay=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1')
summary=importlib.import_module('eval.experiments.campaign-gap-audit.summarize')


def bound_result(receipt, original):
    if receipt['config']['function']!=original['function']:
        raise ValueError('worker function identity mismatch')
    if receipt['root']['source_sha256']!=original['source_sha256']:
        raise ValueError('worker did not start from selected final source')
    result=receipt['result']
    if result['calls_attempted'] or result.get('generations'):
        raise ValueError('zero-model replay made model calls')
    body=Path(result['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(body.encode()).hexdigest()!=result['best_source_sha256']:
        raise ValueError('best source identity mismatch')
    semantic=result.get('semantic_validation') or {}
    if semantic and semantic.get('source_sha256')!=result['best_source_sha256']:
        raise ValueError('semantic source identity mismatch')
    residual=result['best_residual']
    if result['exact']:
        certificate=result.get('verification') or {}
        if not residual['exact'] or not certificate.get('exact'):
            raise ValueError('exactness claim lacks agreeing certificate')
        if certificate.get('candidate_source_sha256')!=result['best_source_sha256']:
            raise ValueError('exactness certificate source mismatch')
    return result, body


def stage(result):
    residual=result['best_residual']
    semantic=result.get('semantic_validation') or {}
    if residual.get('compiled') is not True:
        return 'not_compiling'
    if (residual.get('frontend') or {}).get('passed') is not True:
        return 'frontend_rejected_or_unavailable'
    if result['exact']:
        return 'accepted_object_exact'
    if semantic.get('status')=='observed_pass':
        return 'sampled_pass_nonexact'
    if semantic.get('status')=='observed_pass_with_execution_debt':
        return 'sampled_pass_with_debt_nonexact'
    if (semantic.get('counts') or {}).get('failed',0):
        return 'differential_disagreement'
    if (semantic.get('counts') or {}).get('inconclusive',0) or semantic.get('status')=='inconclusive':
        return 'semantic_inconclusive'
    return 'semantic_untested_or_unavailable'


def trial_evidence(panel):
    trials=panel.get('exploration_trials')
    if trials is None:
        return {'status':'not_recorded','scope':'older receipt or uninitialized panel; no all-trial claim'}
    counts=trials['status_counts']
    attempted=trials['attempted_cases']
    if type(attempted) is not int or attempted<0 or any(type(n) is not int or n<0 for n in counts.values()) or sum(counts.values())!=attempted:
        raise ValueError('exploration trial counts do not account for attempts')
    if len(trials['noncompleted_examples'])>8:
        raise ValueError('exploration examples exceed recorded bound')
    return {'status':'counts_verified',**trials}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    root=replay.ROOT/'eval/results'
    path=args.receipt or root/(replay.PREFIX+'.json')
    output=args.out or path.with_name(path.stem+'-audit.json')
    if output.exists():
        raise ValueError('refusing to overwrite audit')
    report=json.loads(path.read_text())
    if report['status'] not in {'finished','finished_with_errors'}:
        raise ValueError('replay not terminal')
    manifest_path=Path(report['manifest'])
    if replay.sha(manifest_path)!=report['manifest_sha256']:
        raise ValueError('preflight changed')
    manifest=json.loads(manifest_path.read_text())
    frozen_wavefront.verify_files(manifest['pins'])
    if replay.sha(Path(manifest['baseline']))!=manifest['baseline_sha256']:
        raise ValueError('immutable baseline changed')
    selected={r['function']:r for r in manifest['selection']}
    if len(report['rows'])!=16 or {r['function'] for r in report['rows']}!=set(selected):
        raise ValueError('incomplete or duplicated replay cohort')
    rows=[]
    with sqlite3.connect(f"file:{manifest['db']}?mode=ro",uri=True) as conn:
        for row in report['rows']:
            name=row['function']
            original=selected[name]
            if row['outcome']=='execution_error':
                rows.append({'function':name,'stage':'execution_error',
                    'error':row['error'],'accounting_status':'unclassified'})
                continue
            if row['outcome']=='intake_boundary_rechecked':
                rows.append({'function':name,'stage':'parked','intake':row['intake'],
                    'accounting':summary.accounting({'blocker':row['intake']},'parked',None)})
                continue
            artifact=Path(row['receipt'])
            if replay.sha(artifact)!=row['receipt_sha256']:
                raise ValueError('worker receipt changed')
            receipt=json.loads(artifact.read_text())
            result,body=bound_result(receipt,original)
            panel=receipt.get('semantic_panel') or {}
            trials=trial_evidence(panel)
            attempt=conn.execute('SELECT f.name,a.source_code FROM attempts a JOIN functions f '
                'ON f.addr=a.func_addr WHERE a.id=?',(result['best_attempt_id'],)).fetchone()
            if attempt!=(name,body):
                raise ValueError('database attempt does not match best source')
            champions={}
            for label,champion in result.get('champions',{}).items():
                saved=conn.execute('SELECT f.name,a.source_code FROM attempts a JOIN functions f '
                    'ON f.addr=a.func_addr WHERE a.id=?',(champion['attempt_id'],)).fetchone()
                if not saved or saved[0]!=name or hashlib.sha256(saved[1].encode()).hexdigest()!=champion['source_sha256']:
                    raise ValueError('champion database/source mismatch')
                semantic=champion.get('semantic') or {}
                if semantic and semantic.get('source_sha256')!=champion['source_sha256']:
                    raise ValueError('champion semantic source mismatch')
                champions[label]={key:champion[key] for key in ('attempt_id','source_sha256','score')}
                champions[label].update(semantic_status=semantic.get('status'),counts=semantic.get('counts'),
                    debt=semantic.get('debt'),selected=champion['source_sha256']==result['best_source_sha256'])
            current_stage=stage(result)
            node={'residual':result['best_residual'],'semantic_validation':result.get('semantic_validation')}
            rows.append({'function':name,'stage':current_stage,'receipt':str(artifact),
                'receipt_sha256':row['receipt_sha256'],'source_sha256':result['best_source_sha256'],
                'historical_source_sha256':original['source_sha256'],
                'attempt_id':result['best_attempt_id'],'root_residual':receipt['root']['residual'],
                'best_residual':result['best_residual'],
                'semantic_counts':(result.get('semantic_validation') or {}).get('counts'),
                'target_coverage':(result.get('semantic_validation') or {}).get('target_coverage'),
                'exploration_trials':trials,
                'callee_admission':panel.get('callee_admission'),
                'deterministic_candidates':result.get('deterministic_candidates'),
                'champions':champions,
                'accounting':summary.accounting(node,current_stage,body)})
    audit={'kind':'source-bound-frozen-fix-replay-audit','receipt':str(path),
        'receipt_sha256':replay.sha(path),'rows':rows,
        'stages':dict(Counter(r['stage'] for r in rows)),
        'model_calls':0,'integration_requested':False,'reference_bodies_used':False,
        'immutable_baseline_unchanged':True,'causal_accounting_complete':False,
        'scope':'default machinery on exposed final sources; not unseen transfer, assisted-environment activation, or ROM exactness'}
    agentrepair._atomic_json(output,audit)
    print(json.dumps({'output':str(output),'stages':audit['stages'],'sha256':replay.sha(output)}))


if __name__=='__main__':
    main()

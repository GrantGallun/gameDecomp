"""Prepare a frozen-verifier benchmark handoff; explicit --commit imports it.

Uses private full-history DB, original source parent, real saved model request,
standard compiler/frontend/exact gate, idempotent trajectory merge, and ordinary
campaign acceptance. No model call and no TU/ROM integration are performed.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
RUN = PROJECT/'eval/results/resume-pipeline-20260908'
FROZEN = RUN/'code'
sys.path.insert(0,str(FROZEN))
from eval import campaign_state, campaign_workers, completion_campaign as campaign, fast_campaign
from solver import workspace, modelrepair, residual
from kb import attempts as receipts


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def paused():
    service = read(RUN/'service.json')
    if service.get('status')!='paused' or service.get('worker_pid'):
        raise ValueError('campaign must be paused and drained')


def inputs(state):
    if state.get('fast_inflight') or state.get('inflight'):
        raise ValueError('campaign still has inflight work')
    campaign.frozen_wavefront.verify_files(state['pins'])
    if Path(state['config']['project']).resolve()!=FROZEN.resolve():
        raise ValueError('unexpected frozen campaign code')


def prepare(out):
    paused()
    with campaign.campaign_lock((RUN/'campaign.json').with_suffix('.lock')):
        state = campaign_state.read(RUN/'campaign.json')
        inputs(state)
        node = state['nodes']['initFixedTransform']
        if node['status'] in {'object_exact','integrated'}:
            raise ValueError('function is already exact; no handoff needed')
        replay = PROJECT/'eval/results/optimization-audit-20260911/inference-replay-12'
        manifest = read(replay/'manifest.json')
        template = next(row for row in manifest['rows'] if row['id']==2403)
        saved = read(replay/'2403-compact-medium.json')
        source = (replay/'2403-compact-medium.c').read_text()
        prompt = (replay/'2403-compact-medium.prompt.txt').read_text()
        parent = template['parent_attempt_id']
        if (template['function']!='initFixedTransform' or node['attempt_id']!=parent or
                node['source_sha256']!=sha(template['source_code'].encode()) or
                sha(Path(node['source']).read_bytes())!=node['source_sha256']):
            raise ValueError('current source/parent differs from measured benchmark input')
        if (sha(prompt.encode())!=saved['settings']['prompt_sha256'] or
                saved['metadata']['_prompt_sha256']!=saved['settings']['prompt_sha256'] or
                sha(source.encode())!=saved['validation']['candidate_sha256']):
            raise ValueError('saved benchmark request/candidate hash mismatch')
        proposal = modelrepair.parse_proposal(saved['text'],source=template['source_code'])
        if modelrepair.apply_proposal(template['source_code'],proposal)!=source:
            raise ValueError('frozen source-bound parser does not reproduce exact candidate')
        pin = campaign.digest(state['pins'])
        job_id = 'benchmark-2403-compact-medium-'+sha(source.encode())[:12]+'-'+pin[:12]
        if (out/'prepared.json').exists():
            raise ValueError('preparation already exists; review or use another output directory')
        out.mkdir(parents=True,exist_ok=True)
        native = Path('/home/grant/decomp/exact-benchmark-handoff-20260911')/job_id
        native.mkdir(parents=True,exist_ok=True)
        db = native/'worker.sqlite'
        cutoff = campaign_workers.synchronize(Path(state['config']['db']),db,None)
        repo = campaign_workers.isolate(Path(state['config']['repo']),native/'repo','initFixedTransform')
        ws = workspace.bootstrap(repo,'initFixedTransform')
        source_file = native/'verified-candidate.c'
        source_file.write_text(source)
        provenance = {'kind':'saved-real-benchmark-generation','template_proposal_id':2403,
            'request_receipt':str(replay/'2403-compact-medium.json'),
            'request_receipt_sha256':sha((replay/'2403-compact-medium.json').read_bytes()),
            'manifest_sha256':sha((replay/'manifest.json').read_bytes()),
            'prompt_sha256':sha(prompt.encode()),'candidate_sha256':sha(source.encode()),
            'settings':saved['settings'],'generation_metadata':saved['metadata'],
            'generation_wall_seconds':saved['wall_seconds'],
            'inference_already_performed':True,'new_model_calls_during_handoff':0}
        with sqlite3.connect(db) as conn:
            original = conn.execute('SELECT source_code FROM attempts WHERE id=?',(parent,)).fetchone()
            if original is None or original[0]!=template['source_code']:
                raise ValueError('private DB parent source differs')
            receipts.start_run(conn,job_id,kind='verified-benchmark-handoff',model=saved['settings']['model'],config=provenance)
            proposal_id = receipts.record_model_proposal(conn,run_id=job_id,parent_attempt_id=parent,
                prompt=prompt,raw_response=saved['text'],status='saved-benchmark-applied',
                model=saved['settings']['model'],kind=proposal.kind,hypothesis=proposal.hypothesis,
                edits=[asdict(edit) for edit in proposal.edits],sampling=provenance,
                wall_ms=round(saved['wall_seconds']*1000),token_cost=saved['metadata'].get('eval_count',0))
            started = time.monotonic()
            att = workspace.score(ws,repo,'initFixedTransform_benchmark_verified',source,conn=conn,func='initFixedTransform',
                strategy='verified-benchmark-handoff',model=saved['settings']['model'],prompt=prompt,
                run_id=job_id,run_kind='verified-benchmark-handoff',run_config=provenance,
                parent_attempt_id=parent,relation='model-repair',action=proposal.hypothesis,
                extra={'benchmark_generation':provenance,'model_proposal_id':proposal_id},
                raw_response=saved['text'])
            receipts.link_model_proposal(conn,proposal_id,att.receipt_id)
        if not att.exact or (att.frontend or {}).get('passed') is not True:
            campaign_state.atomic(out/'failed-verification.json',asdict(att))
            raise ValueError('current frozen compiler/frontend/exact gates did not accept benchmark candidate')
        obj = ws/'initFixedTransform_benchmark_verified.o'
        packet = residual.build(att,target_asm=workspace.target_asm(ws,'initFixedTransform'),
            target_object=ws/'target.o',candidate_object=obj).to_dict()
        from eval.semantic_lane import Panel
        panel = Panel(repo,ws,'initFixedTransform',64,10000,5000,None,template['source_code'])
        semantic = panel(modelrepair.CandidateState(source,att,obj))
        campaign_state.atomic(out/'frozen-semantic-panel.json',panel.report)
        source_hash = sha(source.encode())
        champion = {'attempt_id':att.receipt_id,'source_sha256':source_hash,'score':att.score,'semantic':semantic}
        result = {'status':'evaluated','exact':att.exact,'attempt_id':att.receipt_id,'source':str(source_file),
            'source_sha256':source_hash,'score':att.score,'verification':att.verification,'residual':packet,
            'semantic_validation':semantic,'champions':{'byte':champion,'semantic':champion},
            'frontier':[{'attempt_id':att.receipt_id,'source_sha256':source_hash,'score':att.score,'compiled':True,
                'semantic':semantic,'hypotheses':[proposal.hypothesis],'kinds':[proposal.kind]}],
            'calls_attempted':0,'historical_benchmark_calls':1,'best_score_improved':att.score>node['score'],
            'incomplete_responses':0,'invalid_proposals':0,'log':['Real saved benchmark generation reverified by current frozen gates.'],
            'benchmark_generation':provenance,'wall_seconds':time.monotonic()-started}
        profile = {'name':'verified_benchmark_handoff','lane':campaign.repair_queue.lane(node).value,
            'evidence_key':campaign.repair_queue.evidence_key(node),'model':False}
        job = {'id':job_id,'function':'initFixedTransform','db':str(db),'cutoffs':cutoff,'node':node,
            'profile':profile,'raw':str(out/'private-result.json'),'receipt':str(out/'canonical-result.json'),
            'pins':state['pins'],'object_path':str(obj),'object_sha256':sha(obj.read_bytes()),
            'verifier_files':{str(Path(workspace.__file__)):sha(Path(workspace.__file__).read_bytes())}}
        inputs(state)
        campaign_state.atomic(out/'private-result.json',result)
        campaign_state.atomic(out/'prepared.json',job)
        print(json.dumps({'prepared':str(out/'prepared.json'),'exact':att.exact,'frontend_passed':att.frontend['passed'],
            'source_sha256':source_hash,'object_sha256':job['object_sha256'],'parent_attempt_id':parent,
            'private_attempt_id':att.receipt_id,'private_proposal_id':proposal_id,'semantic_counts':semantic.get('counts')}))


def commit(out):
    paused()
    job = read(out/'prepared.json')
    raw = read(out/'private-result.json')
    with campaign.campaign_lock((RUN/'campaign.json').with_suffix('.lock')):
        state = campaign_state.read(RUN/'campaign.json')
        inputs(state)
        node = state['nodes'][job['function']]
        if any(row.get('receipt')==job['receipt'] for row in node['jobs']):
            print(json.dumps({'already_applied':True,'status':node['status']}))
            return
        if state['pins']!=job['pins']:
            raise ValueError('frozen pins changed since preparation; reverify before importing')
        if sha(Path(job['object_path']).read_bytes())!=job['object_sha256']:
            raise ValueError('prepared exact object changed')
        verification = raw.get('verification') or {}
        compiled_source = Path(job['object_path']).with_suffix('.c')
        if (raw.get('exact') is not True or verification.get('exact') is not True or
                verification.get('candidate_sha256')!=job['object_sha256'] or
                verification.get('candidate_source_sha256')!=raw.get('source_sha256') or
                verification.get('source_sha256')!=sha(compiled_source.read_bytes()) or
                raw.get('residual',{}).get('frontend',{}).get('passed') is not True):
            raise ValueError('prepared source/object/certificate/frontend binding differs')
        with sqlite3.connect(Path(job['db']).resolve().as_uri()+'?mode=ro',uri=True) as private:
            recorded = private.execute('SELECT source_sha256,source_code,exact,parent_attempt_id FROM attempts WHERE id=?',
                (raw['attempt_id'],)).fetchone()
            if (recorded is None or recorded[0]!=raw['source_sha256'] or
                    sha(recorded[1].encode())!=raw['source_sha256'] or recorded[2]!=1 or
                    recorded[3]!=job['node']['attempt_id']):
                raise ValueError('private verifier lineage differs from prepared receipt')
        fast_campaign.validate_job(node,job,raw)
        amap,pmap = campaign_workers.merge(Path(state['config']['db']),Path(job['db']),job['cutoffs'],job['id'])
        result = campaign_workers.remap(raw,amap,pmap)
        result['private_lineage'] = {'raw_receipt':job['raw'],'attempt_ids':amap,'proposal_ids':pmap,'dispatch_profile':job['profile']}
        campaign_state.atomic(job['receipt'],result)
        campaign.accept(node,job['profile'],result,Path(job['receipt']))
        selected = fast_campaign.project(state)
        fast_campaign.summary(state,selected)
        campaign_state.Store(RUN/'campaign.json').save(state,changed=(job['function'],))
        campaign_state.atomic(out/'imported.json',{'job_id':job['id'],'attempt_ids':amap,'proposal_ids':pmap,
            'status':node['status'],'source_sha256':node['source_sha256'],'summary':state['summary']})
        print(json.dumps(read(out/'imported.json')))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path(__file__).with_name('exact-handoff-2403'))
    parser.add_argument('--commit',action='store_true')
    options = parser.parse_args()
    (commit if options.commit else prepare)(options.out)

"""Evaluate saved effort-replay candidates on one fixed parent-bound target panel.

No inference calls, campaign imports or shared candidate-workspace mutations.
Full panel and source-bound results remain separate from the compact summary.
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
sys.path.insert(0, str(PROJECT))
from eval import campaign_workers, semantic_lane
from solver import modelrepair, workspace


def sha(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, separators=(',', ':'))+'\n')
    temporary.replace(path)


def brief(result):
    return {key:result.get(key) for key in ('status','source_sha256','panel_sha256',
        'counts','total','semantic_key','passed_case_indices','debt','source_object_obligations',
        'execution_obstructions','reason')} if result else {'status':'not_evaluated'}


def compile_state(repo, ws, function, tag, source):
    started = time.monotonic()
    attempt = workspace.score(ws, repo, tag, source)
    state = modelrepair.CandidateState(source, attempt, ws/(tag+'.o') if attempt.compiled else None)
    return state, {'compiled':attempt.compiled,'frontend_passed':(attempt.frontend or {}).get('passed'),
        'score':attempt.score,'exact':attempt.exact,'seconds':time.monotonic()-started,
        'source_sha256':sha(source.encode()),'object_sha256':sha(state.object_path.read_bytes()) if state.object_path else None,
        'attempt':asdict(attempt),'object_path':str(state.object_path) if state.object_path else None}


def evaluate(panel, state):
    if panel is None:
        return None
    result = panel(state)
    if result and result.get('panel_sha256') != panel.identity:
        raise ValueError('candidate evaluation changed fixed target panel')
    return result


def summarize(rows, arms=('compact-high','compact-medium','compact-low')):
    result = {}
    for arm in arms:
        selected = [row for row in rows.values() if row['arm']==arm]
        result[arm] = {'completed':len(selected),
            'compiled':sum(bool(r.get('compiled')) for r in selected),
            'frontend_passed':sum(r.get('frontend_passed') is True for r in selected),
            'compiled_frontend_passed':sum(bool(r.get('compiled')) and r.get('frontend_passed') is True for r in selected),
            'byte_improved':sum((r.get('byte_delta') or 0)>0 for r in selected),
            'byte_regressed':sum((r.get('byte_delta') or 0)<0 for r in selected),
            'exact':sum(bool(r.get('exact')) for r in selected),
            'semantic_comparable':sum(bool(r.get('semantic_comparable')) for r in selected),
            'lost_passing_cases':sum(bool(r.get('lost_passing_cases')) for r in selected),
            'gained_passing_cases':sum(bool(r.get('gained_passing_cases')) for r in selected),
            'fewer_observed_failures':sum((r.get('observed_failure_delta') or 0)<0 for r in selected),
            'more_observed_failures':sum((r.get('observed_failure_delta') or 0)>0 for r in selected)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path(__file__).with_name('inference-replay-12'))
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--native-root', type=Path, default=Path('/home/grant/decomp/inference-semantic-validation-20260911'))
    parser.add_argument('--watch-seconds', type=int, default=1800)
    parser.add_argument('--arms', default='compact-high,compact-medium,compact-low')
    args = parser.parse_args()
    arms = tuple(args.arms.split(','))
    if not arms or any(arm not in {'original-high','original-medium','original-low','compact-high','compact-medium','compact-low'} for arm in arms):
        raise ValueError('unknown replay arm')
    manifest = json.loads((args.out/'manifest.json').read_bytes())
    native = args.native_root/str(time.time_ns())
    native.mkdir(parents=True)
    baseline, completed = {}, {}
    files = [Path(semantic_lane.__file__),Path(workspace.__file__),PROJECT/'solver/mips_differential.py',
             PROJECT/'solver/callee_execution.py',PROJECT/'eval/dag_pipeline_pilot.py']
    code_hashes = {str(path):sha(path.read_bytes()) for path in files}
    started = time.monotonic()
    expected = len(manifest['rows'])*len(arms)
    while len(completed)<expected:
        progress = False
        for row in manifest['rows']:
            for arm in arms:
                key = f"{row['id']}-{arm}"
                receipt = args.out/(key+'.json')
                if key in completed or not receipt.exists():
                    continue
                try:
                    candidate_record = json.loads(receipt.read_bytes())
                except json.JSONDecodeError:
                    continue
                if 'validation' not in candidate_record and 'error' not in candidate_record:
                    continue  # provider receipt is durable before compiler validation
                validation = candidate_record.get('validation') or {}
                result = {'proposal_id':row['id'],'function':row['function'],'arm':arm,
                    'provider_seconds':candidate_record.get('wall_seconds')}
                candidate_path = receipt.with_suffix('.c')
                if not validation.get('applied') or not candidate_path.exists():
                    result.update(status='no_applied_candidate',reason=validation.get('error') or candidate_record.get('error'))
                    completed[key] = result
                    progress = True
                    continue
                function = row['function']
                if row['id'] not in baseline:
                    repo = campaign_workers.isolate(args.repo, native/str(row['id']), function)
                    ws = workspace.bootstrap(repo, function)
                    with sqlite3.connect((PROJECT/'eval/results/resume-pipeline-20260908/campaign.sqlite').resolve().as_uri()+'?mode=ro', uri=True) as db:
                        workspace.configure_compiler(ws, repo, db, function)
                    parent, parent_receipt = compile_state(repo, ws, function, function+'_baseline', row['source_code'])
                    panel_start = time.monotonic()
                    try:
                        panel = semantic_lane.Panel(repo, ws, function, 64, 10000, 5000, None, row['source_code'])
                        panel_report = panel.report
                        parent_semantic = evaluate(panel, parent)
                    except (OSError, ValueError, KeyError) as exc:
                        panel = None
                        parent_semantic = None
                        panel_report = {'status':'unavailable','reason':f'{type(exc).__name__}: {exc}'}
                    parent_receipt.update(semantic=parent_semantic,panel_seconds=time.monotonic()-panel_start)
                    atomic(args.out/f"{row['id']}-panel.json", panel_report)
                    atomic(args.out/f"{row['id']}-baseline-semantic.json", parent_receipt)
                    baseline[row['id']] = dict(repo=repo,ws=ws,panel=panel,receipt=parent_receipt,panel_report=panel_report,parent_state=parent)
                base = baseline[row['id']]
                source = candidate_path.read_text()
                if sha(source.encode()) != validation.get('candidate_sha256'):
                    raise ValueError('candidate source differs from inference validation receipt: '+key)
                state, candidate = compile_state(base['repo'],base['ws'],function,function+'_'+arm.replace('-','_'),source)
                # A failed baseline may not have caused build.sh to dump the
                # target yet. The first compiling child can supply that target
                # artifact, but the panel still uses the original parent ABI
                # context and becomes fixed before any candidate execution.
                if (base['panel'] is None and candidate['compiled'] and
                        base['panel_report'].get('reason','').startswith('FileNotFoundError:')):
                    try:
                        base['panel'] = semantic_lane.Panel(base['repo'],base['ws'],function,64,10000,5000,None,row['source_code'])
                        base['panel_report'] = base['panel'].report
                        base['receipt']['semantic'] = evaluate(base['panel'],base['parent_state'])
                    except (OSError,ValueError,KeyError) as exc:
                        base['panel'] = None
                        base['panel_report'] = {'status':'unavailable','reason':f'{type(exc).__name__}: {exc}'}
                    atomic(args.out/f"{row['id']}-panel.json",base['panel_report'])
                    atomic(args.out/f"{row['id']}-baseline-semantic.json",base['receipt'])
                semantic_start = time.monotonic()
                semantics = evaluate(base['panel'],state)
                candidate.update(semantic=semantics,semantic_seconds=time.monotonic()-semantic_start,
                    provider_receipt=str(receipt),panel_receipt=str(args.out/f"{row['id']}-panel.json"))
                atomic(args.out/(key+'-semantic.json'),candidate)
                parent = base['receipt']
                parent_semantic = parent.get('semantic')
                comparable = bool(parent_semantic and semantics and
                    parent_semantic.get('panel_sha256')==semantics.get('panel_sha256') and
                    parent_semantic.get('counts') is not None and semantics.get('counts') is not None)
                result.update(status='evaluated',compiled=candidate['compiled'],frontend_passed=candidate['frontend_passed'],
                    score=candidate['score'],exact=candidate['exact'],baseline_score=parent['score'],
                    baseline_compiled=parent['compiled'],baseline_frontend_passed=parent['frontend_passed'],
                    byte_delta=candidate['score']-parent['score'] if candidate['compiled'] and parent['compiled'] else None,
                    semantic=brief(semantics),baseline_semantic=brief(parent_semantic),semantic_comparable=comparable,
                    panel_unavailable=base['panel_report'] if base['panel'] is None else None)
                if comparable:
                    old = set(parent_semantic.get('passed_case_indices',[]))
                    new = set(semantics.get('passed_case_indices',[]))
                    result.update(lost_passing_cases=sorted(old-new),gained_passing_cases=sorted(new-old),
                        observed_failure_delta=semantics['counts'].get('failed',0)-parent_semantic['counts'].get('failed',0),
                        added_debt=sorted(set(semantics.get('debt',[]))-set(parent_semantic.get('debt',[]))))
                completed[key] = result
                progress = True
                print(json.dumps({k:result.get(k) for k in ('proposal_id','arm','compiled','frontend_passed','byte_delta','semantic_comparable','lost_passing_cases','gained_passing_cases')}),flush=True)
        status = 'complete' if len(completed)==expected else 'waiting'
        report = {'kind':'fixed-parent-panel-effort-comparison','status':status,'completed':len(completed),'expected':expected,
            'native_root':str(native),'evaluator_hashes':code_hashes,'summary':summarize(completed,arms),'rows':completed,
            'scope':'DEV same target-led finite panel per saved parent across all efforts; no universal semantic proof',
            'model_calls':0,'campaign_imports':0,'elapsed_seconds':time.monotonic()-started}
        if any(sha(Path(path).read_bytes())!=digest for path,digest in code_hashes.items()):
            report['status']='evaluator_changed_requires_replay'
            atomic(args.out/'semantic-quality.json',report)
            raise ValueError('evaluator changed while measuring')
        atomic(args.out/'semantic-quality.json',report)
        if len(completed)==expected or time.monotonic()-started>args.watch_seconds:
            print(json.dumps({'status':report['status'],'summary':report['summary']}),flush=True)
            return
        if not progress:
            time.sleep(3)


if __name__ == '__main__':
    main()

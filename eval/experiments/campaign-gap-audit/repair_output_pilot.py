"""Six-call DEV smoke: patch vs function body, plus compact feedback patch."""
import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from eval import agentrepair, frozen_wavefront, semantic_lane
from kb import attempts
from solver import llm, modelrepair, project_headers, repair_context, residual, type_transaction, workspace

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'eval/results/repair-output-pilot-20260908'


def validate_output(source, function, text, arm):
    original, end = repair_context.definition(source, function)
    if arm == 'full_function':
        value = next(modelrepair._objects(text), None)
        if not isinstance(value, dict) or not isinstance(value.get('function'), str):
            raise ValueError('missing function text')
        replacement = value['function']
        match, stop = repair_context.definition(replacement, function)
        if replacement[:match.start()].strip() or replacement[stop:].strip():
            raise ValueError('response must contain only the selected function')
        if (not isinstance(value.get('hypothesis'), str) or not value['hypothesis'].strip()
                or len(value['hypothesis']) > 400 or value.get('kind') not in modelrepair.KINDS):
            raise ValueError('invalid hypothesis or kind')
        candidate = source[:original.start()] + replacement[match.start():stop] + source[end:]
    else:
        proposal = modelrepair.parse_proposal(text, source=source)
        candidate = modelrepair.apply_proposal(source, proposal)
    new, stop = repair_context.definition(candidate, function)
    if candidate[:new.start()] != source[:original.start()] or candidate[stop:] != source[end:]:
        raise ValueError('changes outside selected function')
    signature = type_transaction.signature(source[original.start():end], function)
    if signature is None or type_transaction.signature(candidate[new.start():stop], function) != signature:
        raise ValueError('public signature changed')
    if modelrepair._introduces_escape(source, candidate):
        raise ValueError('source escape')
    if modelrepair._semantic_text(source) == modelrepair._semantic_text(candidate):
        raise ValueError('no semantic source change')
    return candidate


def main():
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    OUT = parser.parse_args().output.resolve()
    context = importlib.import_module('eval.experiments.campaign-gap-audit.repair_output_context')
    original = Path('/home/grant/decomp/sbk1')
    live = json.loads((ROOT / 'eval/results/resume-pipeline-20260908/campaign.json').read_text())
    older = json.loads((ROOT / 'eval/results/failure-coverage-fresh-paired-v7-batch-1.json').read_text())
    audio = json.loads((ROOT / 'eval/results/alSynSetFXMix-resilient-repair-v3.json').read_text())
    selection = [('alSynSetFXMix', {'source': audio['result']['best_source_path'],
                                  'source_sha256': audio['result']['best_source_sha256']}),
                 ('osCreatePiManager', older['nodes']['osCreatePiManager'])]
    pins = live['pins']
    frozen_wavefront.verify_files(pins)
    OUT.mkdir(exist_ok=False)
    endpoint = llm.host()
    model = 'gpt-oss:20b'
    report = {'kind': 'repair-output-and-feedback-smoke', 'status': 'running',
              'selection': 'preselected DEV: alSynSetFXMix and osCreatePiManager saved behavioral-failure candidates',
              'model_digest': frozen_wavefront.model_digest(endpoint, model),
              'config': {'seed': 20260908, 'temperature': 0.2, 'think': 'low', 'num_predict': 4096,
                         'timeout': 180, 'calls_per_arm_per_case': 1, 'semantic_cases': 64,
                         'semantic_steps': 3000, 'exploration_cases': 128},
              'scope': 'six logical calls, exposed DEV, independent fixed parents; no repair search or deterministic child normalization',
              'cases': [], 'integration_requested': False}
    def save():
        (OUT / 'report.json').write_text(json.dumps(report, indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='repair-output-pilot-') as tmp:
        repo = Path(tmp) / 'repo'
        repo.mkdir()
        for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile', 'symbol_addrs.txt',
                     'snowboardkids.yaml', 'snowboardkids.z64', 'build',
                     'undefined_syms_auto.txt', 'undefined_syms.txt'):
            if (original / name).exists():
                (repo / name).symlink_to(original / name, target_is_directory=(original / name).is_dir())
        database = Path(tmp) / 'pilot.sqlite'
        shutil.copy2(ROOT / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite', database)
        db = sqlite3.connect(database, timeout=120)
        for case_index, (function, historical) in enumerate(selection):
            agentrepair._refuse_frozen_heldout(ROOT / 'eval/sets', function)
            source_path = Path(historical['source'])
            source = source_path.read_text()
            if hashlib.sha256(source.encode()).hexdigest() != historical['source_sha256']:
                raise ValueError('selected source changed')
            case = {'function': function, 'historical_source': str(source_path),
                    'parent_sha256': historical['source_sha256'], 'arms': []}
            report['cases'].append(case)
            folder = OUT / function
            folder.mkdir()
            (folder / 'parent.c').write_text(source)
            ws = repo / 'nonmatchings' / function
            ws.mkdir(parents=True)
            for path in (original / 'nonmatchings' / function).iterdir():
                if path.is_file() and (path.suffix == '.py' or path.name.startswith('target')
                        or path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
                    shutil.copy2(path, ws / path.name)
            parent = workspace.score(ws, repo, 'pilot_parent', source, conn=db, func=function,
                                     strategy='repair-output-pilot-parent')
            case['parent'] = {'compiled': parent.compiled, 'exact': parent.exact,
                              'frontend': parent.frontend, 'score': parent.score}
            if not parent.compiled or not (parent.frontend or {}).get('passed'):
                case['status'] = 'preflight_excluded_compile_or_frontend'
                save()
                print(json.dumps({'function':function,'status':case['status']}), flush=True)
                continue
            panel = semantic_lane.Panel(repo, ws, function, max_cases=64, max_steps=3000,
                                        exploration_cases=128, header_source=source)
            root_state = modelrepair.CandidateState(source, parent, ws / 'pilot_parent.o')
            semantic = panel(root_state)
            case['parent']['semantic'] = semantic
            (folder / 'panel.json').write_text(json.dumps(panel.report, indent=2))
            if semantic.get('status') not in {'observed_failure', 'observed_pass', 'observed_pass_with_execution_debt'}:
                case['status'] = 'preflight_excluded_semantic_unavailable'
                save()
                continue
            asm = workspace.target_asm(ws, function)
            header = project_headers.repair_context(repo, source)
            byte_packet = asdict(residual.build(parent, target_asm=asm, target_object=ws/'target.o', candidate_object=ws/'pilot_parent.o'))
            arms = ['patch', 'full_function', 'compact_patch'] if case_index == 0 else ['full_function', 'patch', 'compact_patch']
            for arm in arms:
                packet_fields = ('status', 'counts', 'total', 'debt', 'feedback', 'operation_gradient',
                    'call_contracts', 'callee_source_contracts', 'callee_environment',
                    'source_object_obligations', 'opaque_stack_obligations', 'indirect_call_obligations',
                    'unknown_direct_argument_evidence')
                packet = {key: semantic[key] for key in packet_fields if key in semantic}
                feedback = context.build_feedback(semantic_report=packet, source=source,
                    target_assembly=asm, header_context=header, abi=panel.abi,
                    mode='compact' if arm == 'compact_patch' else 'full')
                objective = ('Fix the observed behavioral disagreement.' if semantic['status'] == 'observed_failure'
                             else 'Preserve sampled behavior and improve exact target object matching.')
                common = (objective + '\nChange only the selected C function, preserving its signature and all code outside it. '
                          'No inline assembly, include changes, pragmas, or reference source. Keep C89. '
                          'Give one testable source correction. Output JSON only.\n')
                if arm == 'full_function':
                    policy = 'Return {"kind":"other","hypothesis":"brief explanation","function":"complete replacement function definition"}. Regenerate the entire selected function, including signature and body.\n'
                    schema = {'type':'object','required':['kind','hypothesis','function'],'additionalProperties':False,
                              'properties':{'kind':{'type':'string','enum':sorted(modelrepair.KINDS)},'hypothesis':{'type':'string','minLength':1,'maxLength':400},'function':{'type':'string'}}}
                else:
                    policy = 'Return {"kind":"expression","hypothesis":"brief explanation","edits":[{"old":"exact unique current source text","new":"replacement"}]}. Use 1-4 edits, at most 4000 old+new characters. Do not replace the whole function or unchanged code.\n'
                    schema = copy.deepcopy(modelrepair.EDIT_SCHEMA)
                    schema['properties']['edits']['items'] = {'type':'object','required':['old','new'],'additionalProperties':False,
                                                             'properties':{'old':{'type':'string','minLength':1},'new':{'type':'string'}}}
                prompt = common + policy + feedback + '\nBYTE RESIDUAL (diagnostic similarity, not correctness):\n' + json.dumps(byte_packet)
                workspace.assert_uncontaminated(prompt, repo, function)
                (folder / (arm + '.prompt.txt')).write_text(prompt)
                row = {'arm':arm,'seed':20260908 + case_index, 'prompt_chars':len(prompt),
                       'feedback_sha256':hashlib.sha256(feedback.encode()).hexdigest()}
                case['arms'].append(row)
                save()
                started = time.monotonic()
                try:
                    text, meta = llm.generate(endpoint, model, prompt, timeout=180, think='low',
                        temperature=0.2, num_predict=4096, seed=row['seed'], response_schema=schema,
                        cache_dir=OUT/'generation-cache', cache_namespace='repair-output-pilot-20260908')
                    row.update(meta=meta, response=text, wall_seconds=time.monotonic()-started)
                    (folder / (arm + '.response.txt')).write_text(text)
                    if meta.get('done_reason') == 'length':
                        raise ValueError('response exhausted output budget')
                    candidate = validate_output(source, function, text, arm)
                    row['status'] = 'valid'
                except Exception as exc:
                    row.update(status='invalid_or_generation_error', error=f'{type(exc).__name__}: {exc}',
                               wall_seconds=time.monotonic()-started)
                    candidate = None
                proposal_id = attempts.record_model_proposal(db, run_id='repair-output-pilot', parent_attempt_id=parent.receipt_id,
                    prompt=prompt, raw_response=row.get('response', row.get('error','')), status=row['status'], model=model,
                    kind=arm, sampling={'seed':row['seed'],'meta':row.get('meta',{})},
                    wall_ms=int(row['wall_seconds']*1000), token_cost=int(row.get('meta',{}).get('eval_count',0)))
                if candidate is not None:
                    (folder / (arm + '.c')).write_text(candidate)
                    attempt = workspace.score(ws, repo, arm, candidate, conn=db, func=function,
                        strategy='repair-output-pilot:' + arm, parent_attempt_id=parent.receipt_id,
                        extra={'proposal_id':proposal_id})
                    attempts.link_model_proposal(db, proposal_id, attempt.receipt_id)
                    state = modelrepair.CandidateState(candidate, attempt, ws/(arm+'.o'))
                    row.update(compiled=attempt.compiled, frontend=attempt.frontend, exact=attempt.exact,
                               score=attempt.score, verification=attempt.verification,
                               semantic=panel(state), compiler_error=attempt.compiler_stderr)
                save()
                print(json.dumps({'function':function,'arm':arm,'status':row['status'],
                                  'exact':row.get('exact'),'score':row.get('score'),
                                  'semantic_counts':(row.get('semantic') or {}).get('counts')}), flush=True)
            case['status'] = 'complete'
        db.close()
        shutil.copy2(database, OUT / 'attempts.sqlite')
    frozen_wavefront.verify_files(pins)
    report.update(status='complete', live_pins_unchanged_after=True)
    save()


if __name__ == '__main__':
    main()

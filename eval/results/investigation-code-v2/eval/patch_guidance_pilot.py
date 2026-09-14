"""Fixed-parent, paired DEV replay of missing-location model failures."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

from eval import agentrepair, frozen_wavefront
from kb import attempts
from solver import llm, modelrepair, patch_guidance, plateau, repair, workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--count', type=int, default=6)
    parser.add_argument('--max-proposal-id', type=int, default=1212)
    args = parser.parse_args()
    original = Path('/home/grant/decomp/sbk1')
    campaign = ROOT / 'eval/results/resume-pipeline-20260908'
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    history = sqlite3.connect((campaign / 'campaign.sqlite').as_uri() + '?mode=ro', uri=True)
    rows = history.execute('SELECT p.id,p.prompt_context,p.raw_response,p.sampling,'
        'a.source_code,f.name FROM model_proposals p JOIN attempts a ON a.id=p.parent_attempt_id '
        'JOIN functions f ON f.addr=a.func_addr WHERE p.id<=? AND p.status=? ORDER BY p.id DESC',
        (args.max_proposal_id, 'invalid')).fetchall()
    history.close()
    selected = []
    seen = set()
    for ident, prompt, raw, sampling, source, function in rows:
        value = next(modelrepair._objects(raw), None)
        if function in seen or not isinstance(value, dict):
            continue
        edits = value.get('edits')
        if not isinstance(edits, list) or not any(isinstance(e, dict) and
                e.get('old', '') == '' and e.get('slot', '') == '' for e in edits):
            continue
        if not prompt.startswith('You are correcting one C candidate') or 'Make ONE narrow' not in prompt:
            continue  # Different plan/semantic schemas are a separate experiment.
        agentrepair._refuse_frozen_heldout(ROOT / 'eval/sets', function)
        selected.append((ident, prompt, source, function, json.loads(sampling)))
        seen.add(function)
        if len(selected) >= args.count:
            break
    if len(selected) != args.count:
        raise ValueError('not enough eligible fixed-parent cases')
    endpoint, model = llm.host(), 'gpt-oss:20b'
    report = {'kind': 'early-patch-guidance-paired-dev', 'status': 'running',
        'selection': 'most recent missing-location invalid per unique function, narrow edit prompts only',
        'max_historical_proposal_id': args.max_proposal_id,
        'scope': 'failure-enriched exposed DEV; not an unbiased model or whole-game success rate',
        'model': model, 'model_digest': frozen_wavefront.model_digest(endpoint, model),
        'config': {'calls_per_arm_per_case': 1, 'num_predict': 4096, 'temperature': .35,
                   'think': 'low', 'timeout': 240, 'num_thread': 4},
        'integration_requested': False, 'cases': [],
        'code_hashes': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                        ('solver/patch_guidance.py', 'solver/modelrepair.py', 'eval/patch_guidance_pilot.py')}}
    def save():
        pending = out / 'report.pending.json'
        pending.write_text(json.dumps(report, indent=2))
        pending.replace(out / 'report.json')
    for index, (ident, prompt, source, function, sampling) in enumerate(selected):
        folder = out / function
        folder.mkdir()
        (folder / 'original.c').write_text(source)
        (folder / 'baseline.prompt.txt').write_text(prompt)
        (folder / 'guidance.prompt.txt').write_text(patch_guidance.prepend(prompt))
        report['cases'].append({'function': function, 'historical_proposal_id': ident,
            'source_sha256': repair._digest(source), 'seed': sampling.get('seed') or 20260910 + index,
            'arms': {}})
    save()  # Freeze all parents/prompts before any new model output is seen.
    for index, case in enumerate(report['cases']):
        function = case['function']
        folder = out / function
        source = (folder / 'original.c').read_text()
        repo = folder / 'repo'
        repo.mkdir()
        for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile', 'symbol_addrs.txt',
                     'snowboardkids.yaml', 'snowboardkids.z64', 'build',
                     'undefined_syms_auto.txt', 'undefined_syms.txt'):
            path = original / name
            if path.exists():
                (repo / name).symlink_to(path, target_is_directory=path.is_dir())
        ws = repo / 'nonmatchings' / function
        ws.mkdir(parents=True)
        for path in (original / 'nonmatchings' / function).iterdir():
            if path.is_file() and (path.suffix == '.py' or path.name.startswith('target') or
                    path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
                shutil.copy2(path, ws / path.name)
        db = sqlite3.connect(folder / 'attempts.sqlite')
        baseline_db = sqlite3.connect((ROOT / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite').as_uri()+'?mode=ro', uri=True)
        baseline_db.backup(db)
        baseline_db.close()
        parent = workspace.score(ws, repo, 'parent', source, conn=db, func=function,
                                 strategy='patch-guidance-pilot-parent')
        case['baseline_attempt'] = asdict(parent)
        for arm in (('baseline', 'guidance') if index % 2 == 0 else ('guidance', 'baseline')):
            prompt = (folder / f'{arm}.prompt.txt').read_text()
            workspace.assert_uncontaminated(prompt, repo, function)
            result = {'status': 'generation_error', 'prompt_sha256': repair._digest(prompt)}
            start = time.monotonic()
            candidate = None
            try:
                response, meta = llm.generate(endpoint, model, prompt, timeout=240, think='low',
                    num_thread=4, temperature=.35, num_predict=4096, seed=case['seed'],
                    response_schema=modelrepair.EDIT_SCHEMA, cache_dir=out / 'cache',
                    cache_namespace='patch-guidance-pilot-20260910')
                result.update(response=response, meta=meta, status='invalid')
                (folder / f'{arm}.response.txt').write_text(response)
                if meta.get('done_reason') == 'length':
                    result['status'] = 'incomplete'
                else:
                    proposal = modelrepair.parse_proposal(response, source=source, truncate_hypothesis=True)
                    candidate = modelrepair.apply_proposal(source, proposal)
                    result['status'] = 'application_valid'
            except Exception as exc:
                result['error'] = f'{type(exc).__name__}: {exc}'
            result['generation_seconds'] = time.monotonic() - start
            proposal_id = attempts.record_model_proposal(db, run_id='patch-guidance-pilot',
                parent_attempt_id=parent.receipt_id, prompt=prompt,
                raw_response=result.get('response', result.get('error', '')), status=result['status'],
                model=model, kind=arm, sampling={'seed': case['seed'], 'meta': result.get('meta', {})},
                wall_ms=int(result['generation_seconds'] * 1000),
                token_cost=int(result.get('meta', {}).get('eval_count', 0)))
            if candidate is not None:
                att = workspace.score(ws, repo, arm, candidate, conn=db, func=function,
                    strategy='patch-guidance-pilot:' + arm, parent_attempt_id=parent.receipt_id)
                attempts.link_model_proposal(db, proposal_id, att.receipt_id)
                (folder / f'{arm}.c').write_text(candidate)
                result.update(compiled=att.compiled, frontend_pass=(att.frontend or {}).get('passed') is True,
                    score=att.score, exact=plateau.verified(repair._State(candidate, att)),
                    attempt=asdict(att))
            case['arms'][arm] = result
            save()
            print(json.dumps({'function': function, 'arm': arm, 'status': result['status'],
                              'compiled': result.get('compiled'), 'frontend_pass': result.get('frontend_pass'),
                              'exact': result.get('exact'), 'error': result.get('error')}), flush=True)
        db.close()
    report['status'] = 'complete'
    save()


if __name__ == '__main__':
    main()

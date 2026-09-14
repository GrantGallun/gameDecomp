"""Bounded saved-proposal replay. Prepare is read-only; run requires a drained campaign.

Run under the project WSL venv for real isolated compiler/frontend validation.
No candidates or trajectories are imported into the authoritative campaign.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import llm, modelrepair, prompt_budget, type_plan, type_transaction, workspace
from eval.campaign_workers import isolate

MARKER = '\nSEMANTIC REPAIR OBJECTIVE (same frozen target-led panel):\n'


def compact_saved(prompt):
    if MARKER not in prompt:
        return prompt
    start = prompt.index(MARKER) + len(MARKER)
    report, length = json.JSONDecoder().raw_decode(prompt[start:])
    return prompt[:start] + json.dumps(prompt_budget.semantic_summary(report)) + prompt[start + length:]


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Without this flag, prepare offline manifest only.')
    parser.add_argument('--count', type=int, default=12)
    parser.add_argument('--ids', help='Explicit comma-separated saved proposal IDs; overrides count.')
    parser.add_argument('--arms', default='original-high,compact-high,compact-medium,compact-low')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11435')
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--native-root', type=Path, default=Path('/home/grant/decomp/inference-audit-validation-20260911'))
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--legacy-overflow-control', action='store_true',
        help='Experiment-only: original arms may replay pre-guard overflowing prompts; never used by campaign.')
    parser.add_argument('--out', type=Path, default=Path(__file__).with_name('inference-replay'))
    args = parser.parse_args()
    if not 1 <= args.count <= 20 or not 1 <= args.timeout <= 240:
        raise ValueError('count must be1..20 and timeout1..240 seconds')
    arms = args.arms.split(',')
    if not arms or any(x not in ('original-high', 'original-low', 'compact-high', 'compact-medium', 'compact-low') for x in arms):
        raise ValueError('unknown arm')
    campaign = ROOT / 'eval/results/resume-pipeline-20260908'
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_bytes())
    else:
        connection = sqlite3.connect((campaign / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        query = ('SELECT p.*, a.source_code, f.name AS function FROM model_proposals p '
                 'JOIN attempts a ON a.id=p.parent_attempt_id JOIN functions f ON f.addr=a.func_addr '
                 'ORDER BY p.id DESC LIMIT 400')
        rows = [dict(x) for x in connection.execute(query)]
        connection.close()
        ids = set(map(int, args.ids.split(','))) if args.ids else None
        selected, seen = [], set()
        for row in rows:
            if ids is not None:
                if row['id'] not in ids: continue
            elif json.loads(row['sampling']).get('phase') != 'repair' or row['function'] in seen:
                continue
            selected.append(row)
            seen.add(row['function'])
            if ids is None and len(selected) == args.count: break
        if ids is not None and {r['id'] for r in selected} != ids:
            raise ValueError('requested IDs not found in recent400 proposals')
        if not selected or len(selected) > 20: raise ValueError('need1..20 saved proposals')
        manifest = {'version': 1, 'rows': selected, 'scope': 'DEV saved repair prompts; no campaign imports'}
        atomic(manifest_path, manifest)
    estimates = []
    for row in manifest['rows']:
        prompt = row['prompt_context']
        compact = compact_saved(prompt)
        record = {'id': row['id'], 'function': row['function'], 'original_chars': len(prompt), 'compact_chars': len(compact)}
        for label, text in [('original', prompt), ('compact', compact)]:
            try: record[label] = prompt_budget.context_budget(text, 6000, num_ctx=32768)
            except prompt_budget.ContextBudgetError as exc: record[label] = {'declined': True, **exc.context_budget}
        estimates.append(record)
    atomic(args.out / 'estimates.json', estimates)
    if not args.run:
        print(json.dumps({'prepared': len(estimates), 'manifest': str(manifest_path), 'estimates': estimates}))
        return
    for index, row in enumerate(manifest['rows']):
        # Rotate order to reduce a systematic warm-cache advantage for an arm.
        order = arms[index % len(arms):] + arms[:index % len(arms)]
        for arm in order:
            service = json.loads((campaign / 'service.json').read_bytes())
            if service['status'] != 'paused' or service.get('worker_pid'):
                raise ValueError('campaign must be paused and drained before each benchmark request')
            output = args.out / f"{row['id']}-{arm}.json"
            prompt = compact_saved(row['prompt_context']) if arm.startswith('compact-') else row['prompt_context']
            planning = 'Select a connected TYPE PLAN for this decompiler draft' in prompt
            coordinated = 'COORDINATED TYPE REPAIR:' in prompt
            schema = type_plan.SCHEMA if planning else modelrepair.EDIT_SCHEMA
            settings = {'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'seed': json.loads(row['sampling']).get('seed'), 'think': arm.split('-')[1],
                'num_predict': 6000, 'num_ctx': 32768, 'temperature': .35,
                'num_thread': 12, 'timeout': args.timeout, 'transport_attempts': 1,
                'legacy_overflow_control': args.legacy_overflow_control and arm.startswith('original-'),
                'endpoint': args.endpoint, 'schema': schema, 'model': row['model']}
            if output.exists():
                result = json.loads(output.read_bytes())
                if result['settings'] != settings: raise ValueError('resume settings differ from stored request')
            else:
                result = {'proposal_id': row['id'], 'function': row['function'], 'arm': arm, 'settings': settings}
                start = time.monotonic()
                original_budget = prompt_budget.context_budget
                try:
                    if settings['legacy_overflow_control']:
                        def legacy_budget(prompt, num_predict, **kwargs):
                            try:
                                return original_budget(prompt, num_predict, **kwargs)
                            except prompt_budget.ContextBudgetError as exc:
                                return {**exc.context_budget, 'capacity': 32768,
                                    'experimental_overflow_allowed': True,
                                    'scope': 'Legacy overflowing control only; not a production headroom guarantee'}
                        prompt_budget.context_budget = legacy_budget
                    text, meta = llm.generate(args.endpoint, row['model'], prompt, timeout=args.timeout,
                        num_thread=12, num_predict=6000, num_ctx=32768, think=settings['think'],
                        seed=settings['seed'], temperature=.35, response_schema=schema, transport_attempts=1)
                    result.update(text=text, metadata=meta)
                except Exception as exc:
                    result.update(error=str(exc), error_type=type(exc).__name__, context_budget=getattr(exc, 'context_budget', None))
                finally:
                    prompt_budget.context_budget = original_budget
                result['wall_seconds'] = time.monotonic() - start
                atomic(output, result)  # durable response before compiler work; resume never redraws it
            if 'validation' not in result and 'text' in result:
                validation = {}
                try:
                    if result['metadata'].get('done_reason') == 'length' or result['metadata'].get('_fell_back_to_thinking'):
                        raise ValueError('incomplete-response; no final edit')
                    repo = isolate(args.repo, args.native_root / f"{row['id']}-{arm}", row['function'])
                    ws = workspace.bootstrap(repo, row['function'])
                    with sqlite3.connect((campaign / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as context:
                        configured = workspace.configure_compiler(ws, repo, context, row['function'])
                        recipe = configured[1] if configured else {}
                    source = row['source_code']
                    workspace.assert_uncontaminated(prompt, repo, row['function'])
                    if planning:
                        abi = type_transaction.contract(repo, source, row['function'])
                        candidate, plan = type_plan.apply(repo, ws, source, json.loads(result['text']), row['function'], abi, recipe.get('target', ''))
                        validation['plan'] = plan
                    else:
                        proposal = modelrepair.parse_proposal(result['text'], source=source, type_transaction=coordinated)
                        candidate = modelrepair.apply_proposal(source, proposal, type_transaction=coordinated)
                    validation['applied'] = True
                    candidate_path = output.with_suffix('.c')
                    candidate_path.write_text(candidate)
                    attempt = workspace.score(ws, repo, row['function'], candidate)
                    validation.update(compiled=attempt.compiled, frontend_passed=(attempt.frontend or {}).get('passed'),
                        score=attempt.score, exact=attempt.exact, compiler_stderr=attempt.compiler_stderr,
                        candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest())
                except Exception as exc:
                    validation.update(error=str(exc), error_type=type(exc).__name__)
                result['validation'] = validation
                atomic(output, result)
            print(json.dumps({k: result.get(k) for k in ('proposal_id', 'arm', 'wall_seconds', 'error', 'validation')}), flush=True)


if __name__ == '__main__': main()

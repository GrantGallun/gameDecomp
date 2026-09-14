"""Frozen best-source experiments with explicit callback states and OSS last."""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import differential_repair_pilot as pilot, semantic_stress_pilot as stress
from solver import llm, residual_alternatives
from callback_panel import cases as callback_cases


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--function', action='append')
    p.add_argument('--oss', action='store_true')
    p.add_argument('--source', type=Path, help='resume one named function from a verified candidate')
    p.add_argument('--expansions', type=int, default=8)
    args = p.parse_args()
    if args.source and len(args.function or []) != 1:
        p.error('--source requires exactly one --function')
    args.out.mkdir(parents=True, exist_ok=False)
    old = ROOT / 'eval/results/direct-source-semantic-cohort-v4'
    selection = json.loads((old / 'selection.json').read_text())
    repo = Path('/home/grant/decomp/sbk1')
    db = ROOT / 'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite'
    summary = []
    for name in args.function or ('createCallbackTaskPreservingArgs', '__MusIntRandom', 'calculateRaceTimerDelta'):
        item = selection[name]
        census = json.loads(Path(item['census']).read_text())
        node = stress._node(census, name)
        if name == 'createCallbackTaskPreservingArgs':
            cases = callback_cases()
        elif name == '__MusIntRandom':
            saved = json.loads(Path(item['node']['root_semantic_stress']['receipt']).read_text())
            cases = stress._cases_from_rows(saved['panel']['selected_cases'])
        else:
            saved = json.loads(Path(item['node']['child_receipt']).read_text())
            names = {r['case'] for r in saved['result']['differential']['results']}
            cases = tuple(c for c in stress._cases_from_rows(node['target_exploration']['selected_cases']) if c.name in names)
        (args.out / f'{name}.cases.json').write_text(json.dumps([asdict(c) for c in cases], indent=2))
        source = args.source or old / f'{name}.best.c'
        # This database is a different isolated branch: source lineage must not
        # point at an unrelated numerically identical attempt ID.
        parent = item['node']['best_attempt_id']
        common = dict(repo=repo, db=db, model='gpt-oss:20b', endpoint=llm.host() if args.oss else 'unused',
            timeout=180, think='high', num_thread=4, temperature=0, diagnosis_num_predict=3000,
            patch_num_predict=5000, patch_retries=1, compiler_retries=1, max_stalls=1, seed=20260908,
            cache_dir=None, function=name, cases=cases,
            call_arities=node['abi'].get('provisional_call_arities') or {},
            return_registers=tuple(node['abi']['function'].get('return_registers') or []),
            deterministic_only=not args.oss, proposal_replay_budget=0, exactness_expansions=args.expansions)
        def run(path, suffix, rounds, parent_id):
            return pilot.run(**common, source_path=path, source_parent_attempt_id=parent_id,
                output=args.out / f'{name}.{suffix}.json', best_source_out=args.out / f'{name}.{suffix}.c', rounds=rounds)
        print('BASELINE', name, len(cases), flush=True)
        baseline = run(source, 'baseline', 0, parent)
        root = baseline['result']
        row = dict(function=name, cases=len(cases), baseline_score=root['best_attempt']['score'],
            baseline_passed=root['semantic_cases_passed'])
        if not root['all_semantic_cases_passed']:
            attempt = root['best_attempt']
            # Target-backed representation repairs must pass the full panel
            # before any exactness search begins.
            import sqlite3
            with sqlite3.connect(db) as conn:
                diff, sampling = conn.execute('select diff_summary,sampling from attempts where id=?', (attempt['attempt_id'],)).fetchone()
            direct = json.loads(sampling or '{}').get('source_attribution')
            variants = residual_alternatives.representation(source.read_text(), name, diff, direct)
            for i, variant in enumerate(variants):
                path = args.out / f'{name}.repair{i}.seed.c'
                path.write_text(variant.source)
                trial = run(path, f'repair{i}', 0, attempt['attempt_id'])
                if trial['result']['all_semantic_cases_passed']:
                    source, baseline = path, trial
                    row['semantic_repair'] = variant.label
                    break
        if baseline['result']['all_semantic_cases_passed']:
            result = run(source, 'search', 1, baseline['result']['best_attempt']['attempt_id'])
            best = result['result']
            row.update(score=best['best_attempt']['score'], exact=best['exact'], passed=best['semantic_cases_passed'],
                candidates=len(result['deterministic_exactness']), tokens=result['recorded_tokens'],
                termination=result['termination_reason'])
        else:
            row['status'] = 'baseline failed stronger panel; no semantic-clean representation repair'
        summary.append(row)
        (args.out / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()

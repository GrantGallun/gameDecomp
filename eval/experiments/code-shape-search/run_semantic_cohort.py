"""Replay a frozen semantic-pass cohort, then measure deterministic exactness search.

Run under WSL with the production IDO toolchain. Attempts go to a SQLite backup,
sources remain candidate artifacts, and no translation-unit integration occurs.
"""
import json
from dataclasses import asdict
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import differential_repair_pilot as pilot
from solver import code_shapes

FUNCTIONS = ('calculateRaceTimerDelta', 'createCallbackTaskPreservingArgs',
             '__MusIntRandom', 'releaseSoundEffectHandleNode',
             'updateRacePickupIdle', 'Fendit')


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path.home() / 'decomp/sbk1')
    parser.add_argument('--db', type=Path, default=Path.home() / 'decomp/kb-sbk1.sqlite')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--function', action='append', choices=FUNCTIONS)
    parser.add_argument('--attempt-db', type=Path,
                        help='reuse an existing isolated cohort database for a supplemental run')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    functions = tuple(args.function or FUNCTIONS)
    db = args.attempt_db.resolve() if args.attempt_db else out / 'attempts.sqlite'
    if args.attempt_db:
        if not db.is_file() or db == args.db.resolve() or ROOT not in db.parents:
            raise ValueError('supplemental attempt database must be an existing isolated workspace copy')
    else:
        with sqlite3.connect(f'file:{args.db}?mode=ro', uri=True) as original:
            with sqlite3.connect(db) as copy:
                original.backup(copy)
    selected = {}
    for path in sorted((ROOT / 'eval/results').glob('differential-wavefront*.json'),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        wave = json.loads(path.read_text())
        for node in wave.get('nodes', []):
            name = node.get('function')
            if name not in functions or name in selected:
                continue
            if node.get('exact') or not node.get('semantic_settled'):
                continue
            if not node.get('all_observed_semantic_cases_passed'):
                continue
            selected[name] = {'wave': str(path), 'node': node,
                              'census': wave['configuration']['census']}
    if set(selected) != set(functions):
        raise ValueError('missing frozen semantic evidence: ' + str(set(functions) - set(selected)))
    (out / 'selection.json').write_text(json.dumps(selected, indent=2))
    summary = {'kind': 'real-semantic-pass-code-shape-cohort',
               'functions': list(functions), 'deterministic_only': True,
               'attempt_database': str(db), 'max_expansions': 4,
               'max_candidates_per_expansion': 48, 'rows': []}

    def save():
        (out / 'summary.json').write_text(json.dumps(summary, indent=2))

    for name in functions:
        selection = selected[name]
        node = selection['node']
        census = json.loads(Path(selection['census']).read_text())
        cnode = next(n for n in census['dag']['nodes'] if n['function'] == name)
        cases = tuple(pilot.differential.TestCase(
            name=str(r['name']), seed=int(r['seed']),
            player_writes=tuple(tuple(i) for i in r.get('player_writes', [])),
            global_writes=tuple(tuple(i) for i in r.get('global_writes', [])),
            entry_registers=tuple(tuple(i) for i in r.get('entry_registers', [])),
            call_returns=tuple(tuple(i) for i in r.get('call_returns', [])))
            for r in cnode['target_exploration']['selected_cases'])
        if node.get('child_receipt'):
            saved = json.loads(Path(node['child_receipt']).read_text())
            saved_names = {r['case'] for r in saved['result']['differential']['results']}
            expected_count = saved['config']['semantic_case_count']
            if len(saved_names) != expected_count:
                raise ValueError('saved receipt does not contain the complete panel for ' + name)
            cases = tuple(case for case in cases if case.name in saved_names)
            if len(cases) != expected_count:
                raise ValueError('census cannot reconstruct the saved semantic panel for ' + name)
        elif node.get('root_semantic_stress'):
            from eval.semantic_stress_pilot import _cases_from_rows
            saved = json.loads(Path(node['root_semantic_stress']['receipt']).read_text())
            cases = _cases_from_rows(saved['panel']['selected_cases'])
            if len(cases) != saved['panel']['selected_case_count']:
                raise ValueError('incomplete saved stress panel')
        else:
            raise ValueError('no reconstructable saved semantic panel for ' + name)
        if not cases:
            raise ValueError('empty saved semantic panel for ' + name)
        (out / f'{name}.cases.json').write_text(json.dumps(
            [asdict(case) for case in cases], indent=2))
        source = Path(node['best_source']).read_text()
        seed = out / f'{name}.seed.c'
        seed.write_text(source)
        common = dict(repo=args.repo, db=db, source_path=seed,
                      model='gpt-oss:20b', endpoint='unused-deterministic-test',
                      timeout=120, think='high', num_thread=1, temperature=0,
                      diagnosis_num_predict=1, patch_num_predict=1,
                      patch_retries=0, compiler_retries=0, max_stalls=1,
                      seed=20260908, cache_dir=None, function=name, cases=cases,
                      call_arities=cnode['abi'].get('provisional_call_arities') or {},
                      return_registers=tuple(cnode['abi']['function'].get('return_registers') or []),
                      deterministic_only=True, proposal_replay_budget=0)
        row = {'function': name, 'saved_attempt_id': node['best_attempt_id'],
               'saved_score': node['best_score'], 'cases': len(cases),
               'code_shape_options': len(code_shapes.candidates(source, name, 48, allow_do_while=False))}
        summary['rows'].append(row)
        started = time.monotonic()
        print(f'START {name} cases={len(cases)} shapes={row["code_shape_options"]}', flush=True)
        try:
            baseline = pilot.run(**common, rounds=0,
                source_parent_attempt_id=node['best_attempt_id'],
                output=out / f'{name}.baseline.json', best_source_out=out / f'{name}.baseline.c')
            base = baseline['result']
            row.update(baseline_score=base['best_attempt']['score'],
                       baseline_passed=base['semantic_cases_passed'],
                       baseline_exact=base['exact'])
            if not base['all_semantic_cases_passed'] or base['exact']:
                row['status'] = 'excluded-current-baseline-not-semantic-nonexact'
                continue
            receipt = pilot.run(**common, rounds=1,
                source_parent_attempt_id=base['best_attempt']['attempt_id'],
                output=out / f'{name}.search.json', best_source_out=out / f'{name}.best.c')
            result = receipt['result']
            candidates = receipt['deterministic_exactness']
            maps = receipt.get('mismatch_source_maps', [])
            first = maps[0] if maps else {}
            row.update(status='tested', best_score=result['best_attempt']['score'],
                       best_attempt_id=result['best_attempt']['attempt_id'], exact=result['exact'],
                       final_passed=result['semantic_cases_passed'],
                       candidates=len(candidates), compiled=sum(bool(r['attempt']['compiled']) for r in candidates),
                       code_shape_candidates=sum(str(r['label']).startswith('code-shape:') for r in candidates),
                       clean_candidates=sum(bool(r.get('source_intervention', {}).get('semantic_clean')) for r in candidates),
                       direct_candidate_mismatches=first.get('directly_mapped_mismatches', 0),
                       candidate_mismatches=sum(r['side'] == 'candidate' for r in first.get('mismatches', [])),
                       residual_gaps=first.get('gaps', []),
                       compiler_failures=[r['attempt']['compiler_stderr'] for r in candidates if not r['attempt']['compiled']],
                       recorded_tokens=receipt['recorded_tokens'],
                       termination=receipt['termination_reason'], receipt=str(out / f'{name}.search.json'))
        except Exception as exc:
            row.update(status='error', error=f'{type(exc).__name__}: {exc}')
        finally:
            row['wall_seconds'] = round(time.monotonic() - started, 3)
            save()
            print(json.dumps({k: v for k, v in row.items() if k != 'residual_gaps'}), flush=True)
    summary['complete'] = True
    save()


if __name__ == '__main__':
    main()

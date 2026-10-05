"""Verify the private evidence and report costs without rewriting original receipts."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3

HERE = Path(__file__).resolve().parent
inputs = json.loads((HERE / 'inputs.json').read_text())
WORK = Path(inputs['work'])
read = lambda p: json.loads(Path(p).read_text())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

for relative, expected in inputs['code_pins'].items():
    assert sha(Path(inputs['code']) / relative) == expected, relative
for item in inputs['inputs']:
    assert sha(item['source']) == item['source_sha256']

cases, corrections = [], []
trace_count = 0
execution_seconds = 0.0
for item in inputs['inputs']:
    name = item['function']
    result = read(HERE / (name + '.json'))
    events = read(WORK / name / 'trace-events.json')
    calls = 0
    for event in events:
        assert event['available']
        folder = Path(event['directory'])
        assert sha(folder / 'source.c') == event['source_sha256']
        assert all((folder / (stage + '.txt')).stat().st_size > 0
                   for stage in ('ugen', 'level6', 'level5'))
        # The frozen helper performs exactly one compiler invocation per stage.
        if event['calls']:
            assert len(event['calls']) == 3
            assert all(c['returncode'] == 0 for c in event['calls'])
        else:
            assert item['scope'] == 'development'
        calls += 3
    if calls != result['diagnostic_compiles']:
        corrections.append({'function': name, 'recorded_diagnostic_compiles': result['diagnostic_compiles'],
                            'corrected_diagnostic_compiles': calls,
                            'diagnosed_arm_corrected_calls': result['arms'][1]['trace_calls'] * 3,
                            'basis': 'Three nonempty successful stage captures per event, plus frozen traced_compile three-stage loop',
                            'limitation': 'Individual compiler-call timings were not recorded; aggregate capture timing remains available'})
    trace_count += calls
    execution_seconds += result['seconds']
    assert not any(a['exact'] for a in result['arms'])
    assert all(a['best_gradient'] == result['baseline_gradient'] for a in result['arms'])
    cases.append({'function': name, 'scope': item['scope'],
                  'known_exact_control': result['already_exact_at_freeze'],
                  'baseline_gradient': result['baseline_gradient'],
                  'scored_candidates': result['scored_candidates_including_baseline_and_repeat'],
                  'diagnostic_compiler_invocations': calls, 'seconds': result['seconds'],
                  'arms': [{k: a[k] for k in ('arm', 'actual_scored_candidates', 'exact', 'best_gradient', 'seconds')}
                           for a in result['arms']]})

secondary_files = ['randomNextObject-materialization.json',
                   'updateEndingCreditsIdleSparkle-materialization.json',
                   'randomNextObject-increment.json',
                   'updateEndingCreditsIdleSparkle-counter-reread.json']
secondary = []
for filename in secondary_files:
    result = read(HERE / filename)
    execution_seconds += result['seconds']
    calls = sum(r.get('diagnostic_compiles', 0) or 0 for r in result['rows'])
    trace_count += calls
    secondary.append({'artifact': filename, 'function': result['function'],
                      'scored_candidates': len(result['rows']), 'diagnostic_compiler_invocations': calls,
                      'seconds': result['seconds'],
                      'rows': [{k: r.get(k) for k in ('label', 'attempt_id', 'compiled', 'frontend_passed', 'gradient', 'certified')}
                               for r in result['rows']]})

confirmations = []
for filename in ('confirmation.json', 'updateEndingCreditsIdleSparkle-confirmation.json',
                 'updateEndingCreditsIdleSparkle-typed-confirmation.json'):
    result = read(HERE / filename)
    execution_seconds += result['seconds']
    assert sha(result['manifest']) == result['manifest_sha256']
    assert sha(result['receipt']) == result['receipt_sha256']
    manifest = read(result['manifest'])
    receipt = read(result['receipt'])
    assert receipt['manifest_sha256'] == result['manifest_sha256']
    if receipt['whole_rom_verified']:
        assert receipt['status'] == 'rom_exact' and receipt['build_returncode'] == 0
        assert receipt['target_sha256'] == receipt['candidate_sha256']
        assert receipt['target_bytes'] == receipt['candidate_bytes']
        assert receipt['first_difference_offset'] is None
    for replacement in manifest['replacements']:
        assert sha(Path(result['manifest']).parent / replacement['replacement']) == replacement['replacement_sha256']
    confirmations.append({'artifact': filename, 'function': result['function'],
                          'repeats': result['repeats'], 'status': receipt['status'],
                          'whole_rom_verified': receipt['whole_rom_verified'],
                          'proposed_union_count': len(manifest['lineage']),
                          'verified_union_count': len(manifest['lineage']) if receipt['whole_rom_verified'] else None,
                          'seconds': result['seconds'],
                          'receipt': result['receipt'], 'receipt_sha256': result['receipt_sha256'],
                          'target_sha256': receipt.get('target_sha256'),
                          'candidate_sha256': receipt.get('candidate_sha256')})

before = read(HERE.parent / 'integration-headers-20261002/proof.json')
prior = read(before['manifest'])
final = read(read(HERE / 'updateEndingCreditsIdleSparkle-typed-confirmation.json')['manifest'])
lineage_key = lambda row: (row['function'], row['source_sha256'])
assert set(map(lineage_key, prior['lineage'])) <= set(map(lineage_key, final['lineage']))

with sqlite3.connect((WORK / 'trial.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
    count, failed, exact, distinct, first, last = conn.execute('''
        SELECT count(*), sum(compiled=0), sum(exact=1), count(distinct source_sha256),
               min(created_at), max(created_at) FROM attempts''').fetchone()
    certs = []
    for row in conn.execute('''SELECT a.id, f.name, a.parent_attempt_id, a.source_code,
                              a.source_sha256, a.sampling, a.exact
                              FROM attempts a JOIN functions f ON f.addr=a.func_addr ORDER BY a.id'''):
        id_, name, parent, source, expected, sampling, is_exact = row
        assert hashlib.sha256(source.encode()).hexdigest() == expected
        metadata = json.loads(sampling)
        assert metadata['training_eligible'] is False
        if is_exact:
            assert metadata['frontend']['passed']
            assert parent is not None
            certs.append({'attempt_id': id_, 'function': name, 'parent_attempt_id': parent,
                          'source_sha256': expected})
    assert count == sum(c['scored_candidates'] for c in cases) + sum(s['scored_candidates'] for s in secondary) + sum(len(c['repeats']) for c in confirmations)

assert count == 944 and failed == 8 and exact == 8 and distinct == 528
assert trace_count == 111
successful_secondary = [s for s in secondary if any(r['certified'] for r in s['rows'])]
assert [s['scored_candidates'] for s in successful_secondary] == [3, 1]
for filename in ('randomNextObject-increment.json', 'updateEndingCreditsIdleSparkle-counter-reread.json'):
    successful = read(HERE / filename)['rows'][-1]
    assert successful['object_exact'] and successful['frontend_passed']
    assert successful['diagnosis']['non_register'] == 0
    assert successful['diagnosis']['wrong_ranges'] == 0

accounting = {
    'corrections': corrections,
    'cause': 'Initial development counter checked command[0] for trace compiler; actual compiler is nested in the asm-processor command. Follow-up counter checks all command arguments.',
    'original_receipts_preserved': True,
    'legacy_failed_union_field': 'updateEndingCreditsIdleSparkle-confirmation.json verified_union_count=37 is proposed lineage count, not verified. Correct verified count is null for that failed gate.',
    'retracted_interpretation': 'M in the uopt isvar trace denotes a local, not heap memory provenance. The original materialization artifacts preserve the mistaken hypothesis; it was retracted. Ten volatile-view tests yielded no exact.',
}
(HERE / 'accounting-corrections.json').write_text(json.dumps(accounting, indent=2) + '\n')
result = {
    'verdict': 'Narrow causal source-construction theory confirmed in two development cases; broad benefit of diagnosis-guided existing edits unsupported on this five-case panel',
    'confirmed_mechanism': 'Replacing retained narrow-value locals with increment and reread removed wrong coloured local ranges; final normal objects were byte-exact. An initial register-correct variant changed instructions and was rejected.',
    'new_distinct_development_functions': 1, 'reconstructed_known_exact_controls': 1,
    'new_clean_heldout_functions': 0, 'cross_game_transfer_tested': False,
    'production_generator_shipped': False, 'campaign_imported': False, 'training_eligible': False,
    'assistance': 'Game headers and unknown old candidate ancestry; final integration additionally uses measured destination declarations. No reference implementation body supplied.',
    'effort': {'scored_candidate_evaluations': count, 'distinct_source_hashes': distinct,
               'compile_failures': failed, 'exact_attempts_including_repeats': exact,
               'distinct_exact_functions': len({c['function'] for c in certs}),
               'diagnostic_compiler_invocations': trace_count,
               'full_rom_checks': len(confirmations), 'successful_full_rom_checks': 2,
               'accumulated_case_and_confirmation_elapsed_seconds': execution_seconds,
               'elapsed_between_first_and_last_scored_candidate_seconds': last - first,
               'timing_limits': 'Accumulated per-run elapsed times overlap where processes ran in parallel; not CPU time or total engineering effort. Setup and analysis before first candidate excluded. ROM compilation work is reported separately from candidate/diagnostic counts.',
               'model_calls': 0},
    'cases': cases, 'secondary_interventions': secondary,
    'confirmations': confirmations, 'exact_attempt_lineage': certs,
    'retention': {'prior_verified_functions': len(prior['lineage']),
                  'private_verified_union': len(final['lineage']),
                  'all_prior_source_bound_replacements_preserved': True},
    'accounting': accounting,
    'limits': ['Five exposed development/follow-up cases are not an unbiased maturity estimate.',
               'Two successes after adaptive manual hypothesis development do not establish an unattended generator.',
               'Other cases unresolved within bounds; no claim of impossibility.',
               'Some proposal pools exhausted before their budget. The phase-advance case used fewer scored proposals and extra diagnostic cost; no equal-cost advantage claimed.'],
    'next_high_impact_action': 'Implement a narrowly guarded increment-and-reread generator with a positive motivating-fire test; preregister an untouched applicable panel and compare distinct new exacts at equal compiler-call cost before activation.',
    'verified_at_utc': datetime.now(timezone.utc).isoformat(),
}
(HERE / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'verdict': result['verdict'], 'effort': result['effort'], 'retention': result['retention']}, indent=2))

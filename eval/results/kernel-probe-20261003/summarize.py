"""Recompute measurements from successful, non-warmup recorded requests."""
import json
from pathlib import Path
import statistics

root = Path(__file__).resolve().parent
reports = []
baseline_file = root / 'eager_auto.json'
baseline = json.loads(baseline_file.read_text()) if baseline_file.exists() else {}
baseline_rows = {(r['prompt'], r['concurrency'], r['repeat']): r for r in baseline.get('runs', []) if not r['warmup']}
for path in sorted(root.glob('*.json')):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('schema') != 'kernel-probe/1':
        continue
    report = {'variant': data['variant']['name'], 'ok': data['ok'], 'error': data.get('error'),
              'load_seconds': data.get('load_seconds'), 'cases': [],
              'same_prompt_ids_as_baseline': data.get('prompts') == baseline.get('prompts'),
              'same_sampling_as_baseline': data.get('sampling') == baseline.get('sampling'),
              'same_model_config_as_baseline': data.get('model_config_sha256') == baseline.get('model_config_sha256')}
    log = path.with_suffix('.log')
    if log.exists():
        report['kernel_selection_log'] = [line for line in log.read_text(errors='replace').splitlines()
                                          if 'Selected ' in line and 'Kernel for' in line
                                          or 'Using ' in line and 'attention backend' in line
                                          or 'Graph capturing finished' in line
                                          or 'Using FlashAttention version' in line]
    for case in [('short', 1), ('long', 1), ('long', 4)]:
        rows = [r for r in data['runs'] if not r['warmup'] and (r['prompt'], r['concurrency']) == case]
        if not rows:
            continue
        for row in rows:
            assert len(row['requests']) == case[1]
            assert all(q['tokens'] == data['variant']['tokens'] == len(q['token_ids']) for q in row['requests'])
            assert abs(sum(q['tokens'] for q in row['requests']) / row['wall_s'] - row['output_tps_including_prefill']) < 1e-6
        compared = equal = 0
        for row in rows:
            b = baseline_rows.get((*case, row['repeat']))
            if b:
                for request, base in zip(row['requests'], b['requests']):
                    compared += 1
                    equal += request['token_ids_sha256'] == base['token_ids_sha256']
        report['cases'].append({'prompt': case[0], 'concurrency': case[1], 'repeats': len(rows),
                               'decode_tps_median': statistics.median(r['decode_tps_per_request_median'] for r in rows),
                               'decode_tps_min': min(r['decode_tps_per_request_median'] for r in rows),
                               'decode_tps_max': max(r['decode_tps_per_request_median'] for r in rows),
                               'aggregate_tps_including_prefill_median': statistics.median(r['output_tps_including_prefill'] for r in rows),
                               'ttft_s_median': statistics.median(q['ttft_s'] for r in rows for q in r['requests']),
                               'token_sequences_equal_to_baseline': equal, 'token_sequences_compared': compared})
    reports.append(report)
(root / 'summary.json').write_text(json.dumps(reports, indent=2) + '\n')
print(json.dumps(reports, indent=2))

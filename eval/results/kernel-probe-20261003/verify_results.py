"""Verify recorded counts, timings, token hashes, and completed process receipts."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
completed = failed = measured = warmups = 0
for path in sorted(root.glob('*.json')):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('schema') != 'kernel-probe/1':
        continue
    process_path = path.with_name(path.stem + '.process.json')
    process = json.loads(process_path.read_text())
    assert 'exit_code' in process, f'{path.stem}: still running'
    if not data['ok']:
        assert data.get('error') and process['exit_code'] != 0, path.stem
        failed += 1
        continue
    assert process['exit_code'] == 0, path.stem
    completed += 1
    cases = data['variant'].get('cases', ['short:1', 'long:1', 'long:4'])
    assert len(data['runs']) == len(cases) * (data['variant']['repeats'] + 1)
    for case in cases:
        label, raw_n = case.split(':')
        n = int(raw_n)
        rows = [r for r in data['runs'] if r['prompt'] == label and r['concurrency'] == n]
        assert sum(r['warmup'] for r in rows) == 1
        assert sorted(r['repeat'] for r in rows if not r['warmup']) == list(range(data['variant']['repeats']))
        for row in rows:
            warmups += int(row['warmup'])
            measured += int(not row['warmup'])
            assert len(row['requests']) == n
            steps = row['step_elapsed_s']
            assert steps == sorted(steps) and steps[-1] <= row['wall_s']
            for q in row['requests']:
                assert len(q['token_ids']) == q['tokens'] == data['variant']['tokens']
                digest = hashlib.sha256(json.dumps(q['token_ids'], sort_keys=True).encode()).hexdigest()
                assert digest == q['token_ids_sha256']
                assert 0 < q['ttft_s'] < row['wall_s']
                assert 0 < q['decode_s'] < row['wall_s']
                assert abs((q['tokens']-1)/q['decode_s'] - q['decode_tps']) < 1e-6
            total = sum(q['tokens'] for q in row['requests'])
            assert abs(total/row['wall_s'] - row['output_tps_including_prefill']) < 1e-6
result = {'completed_variants': completed, 'recorded_failed_variants': failed,
          'measured_batches_verified': measured, 'warmup_batches_excluded': warmups,
          'checks_passed': True, 'quality_evaluation': False, 'training_eligible': False}
(root / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))

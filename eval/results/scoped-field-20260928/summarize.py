"""Audit retained receipts and write a compact transfer report after all arms finish."""
import difflib
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import accepted

ROOT = Path('/home/grant/decomp/experiments/scoped-field-transfer-20260928')
LOCAL = Path(__file__).parent
REPO = Path('/home/grant/decomp/sbk1')

payload, identity = runner.checked_bundle(ROOT / 'bundle', REPO)
summary = json.loads((ROOT / 'summary.json').read_text())
assert len(summary) == 18, 'comparison not finished'
rows, model_calls, saved = [], [], []
for task in payload['tasks']:
    for arm in ('existing', 'scoped', 'scoped_model'):
        folder = ROOT / task['id'] / arm
        result = json.loads((folder / 'result.json').read_text())
        receipts = [json.loads(line) for line in (folder / 'attempts.jsonl').read_text().splitlines()]
        assert result['valid'] and result['bundle_sha256'] == manifest.fingerprint(payload)
        assert len(receipts) == result['costs']['compiles'] <= 128
        target_sha = manifest.digest(manifest.inside(ROOT / 'bundle', task['target_object']).read_bytes())
        for receipt in receipts:
            artifact = folder / receipt['artifact']
            source = (artifact / 'source.c').read_text()
            assert manifest.digest(source.encode()) == receipt['source_sha256']
            if receipt['exact']:
                cert = receipt['verification']
                assert accepted(cert, receipt['frontend'], source, target_sha)
                assert cert['candidate_sha256'] == manifest.digest((artifact / 'candidate.o').read_bytes())
                dest = LOCAL / 'matches' / task['function'] / arm
                dest.mkdir(parents=True, exist_ok=True)
                for filename in ('source.c', 'candidate.o', 'receipt.json', 'source.frontend.json'):
                    shutil.copyfile(artifact / filename, dest / filename)
                shutil.copyfile(manifest.inside(ROOT / 'bundle', task['target_object']), dest / 'target.o')
                parent = manifest.task_source(ROOT / 'bundle', task)
                (dest / 'change.diff').write_text(''.join(difflib.unified_diff(parent.splitlines(True), source.splitlines(True),
                                                                fromfile='baseline.c', tofile='matched.c')))
                saved.append({'function': task['function'], 'arm': arm, 'label': receipt['label'],
                              'source_sha256': receipt['source_sha256'], 'path': str(dest)})
        rows.append({k: result[k] for k in ('function', 'comparison_arm', 'initial_gradient', 'best_gradient',
                                          'exact', 'costs', 'charged', 'valid', 'assistance')})
        model_calls.extend({'function': task['function'], **r} for r in result['model_calls'])
totals = {}
for arm in ('existing', 'scoped', 'scoped_model'):
    selected = [r for r in rows if r['comparison_arm'] == arm]
    totals[arm] = {'functions': len(selected), 'exact': sum(r['exact'] for r in selected),
        'compiles': sum(r['costs']['compiles'] for r in selected),
        'keys': sum(r['costs']['key_calls'] for r in selected),
        'charged': sum(r['charged'] for r in selected),
        'gradient_improved': sum(tuple(r['best_gradient']) < tuple(r['initial_gradient']) for r in selected)}
report = {'bundle_sha256': manifest.fingerprint(payload), 'totals': totals, 'rows': rows,
          'model_calls': model_calls, 'saved_matches': saved, 'audit_passed': True}
manifest.write_json(LOCAL / 'audited-summary.json', report)
print(json.dumps({'totals': totals, 'model_calls': model_calls, 'matches': saved}, indent=2))

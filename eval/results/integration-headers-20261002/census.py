"""Read-only reach census of pending integration candidates; no ROM verdict inferred."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sqlite3
import sys
import zlib

HERE = Path(__file__).resolve().parent
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
WORK = Path('/home/grant/decomp/experiments/integration-header-census-20261002-v2')
CODE = Path('/home/grant/decomp/experiments/integration-headers-20261002-v4/code')
sys.path.insert(0, str(CODE))
from eval import prepare_integration as new
spec = importlib.util.spec_from_file_location('old_preparer', HERE.parent / 'resume-pipeline-20260908/code/eval/prepare_integration.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def main():
    WORK.mkdir(parents=True, exist_ok=False)
    pointer = json.loads((NATIVE / 'campaign.json').read_bytes())
    with sqlite3.connect((NATIVE / pointer['store']).as_uri() + '?mode=ro', uri=True) as conn:
        raw = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0]
        assert hashlib.sha256(raw).hexdigest() == pointer['sha256']
        manifest = json.loads(raw)
        rows = []
        for name, digest in manifest['nodes'].items():
            raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (digest,)).fetchone()[0])
            assert hashlib.sha256(raw).hexdigest() == digest
            node = json.loads(raw)
            if node['status'] != 'function_exact_pending_integration':
                continue
            entry = {'function': name, **{k: node[k] for k in ('source', 'attempt_id', 'verification')}}
            row = {'function': name, 'source_sha256': node['source_sha256'], 'header_conflicts': []}
            for label, module in [('old', old), ('new', new)]:
                try:
                    path = module.prepare(repo=Path('/home/grant/decomp/sbk1'),
                        db=NATIVE / 'campaign.sqlite', entries=[entry], output_dir=WORK / name / label)
                    data = json.loads(path.read_text())
                    row[label] = 'prepared'
                    row[label + '_source_sha256'] = hashlib.sha256((path.parent / '000.c').read_bytes()).hexdigest()
                    if label == 'new':
                        row['header_conflicts'] = data['lineage'][0].get('header_object_conflicts', {}).get('conflicts', [])
                except (OSError, ValueError) as exc:
                    row[label] = str(exc)[:300]
            rows.append(row)
            (HERE / 'census.json').write_text(json.dumps({'checkpoint': pointer['commit'], 'rows': rows}, indent=2) + '\n')
            print(json.dumps(row), flush=True)
    summary = {'checkpoint': pointer['commit'], 'pending': len(rows),
               'measured_header_conflicts': [r['function'] for r in rows if r['header_conflicts']],
               'new_preparation_blocks': [r['function'] for r in rows if r['old'] == 'prepared' and r['new'] != 'prepared'],
               'scope': 'Preparation reach only; every promotion still requires whole-ROM union verification',
               'training_eligible': False}
    (HERE / 'census-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

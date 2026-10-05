"""Collect small, reviewable receipts from private native canary runs."""
from collections import Counter
import json
from pathlib import Path
import shutil
import sys

NATIVE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/grant/decomp/experiments/investigation-loop-20260927')
OUTPUT = Path(__file__).resolve().parent / 'receipts'


def main():
    OUTPUT.mkdir(exist_ok=True)
    for name in ('manifest.json', 'mechanism-smoke.json'):
        if (NATIVE / name).is_file():
            shutil.copy2(NATIVE / name, OUTPUT / name)
    reports = []
    for folder in [NATIVE, *sorted(p for p in NATIVE.glob('v*') if p.is_dir())]:
        revision = 'initial' if folder == NATIVE else folder.name
        target = OUTPUT / revision
        target.mkdir(exist_ok=True)
        for filename in ('canary-summary.json', 'code-pins.json'):
            if (folder / filename).is_file():
                shutil.copy2(folder / filename, target / filename)
        for path in sorted((folder / 'model').glob('*/result.repair.json')):
            data = json.loads(path.read_text())
            investigation = data.get('investigation') or {}
            events = investigation.get('events', [])
            row = {'revision': revision, 'function': data['config']['function'],
                   'native_receipt': str(path),
                   'actions': dict(Counter(e.get('action', 'generation') for e in events)),
                   'statuses': dict(Counter(e.get('status') for e in events)),
                   'source_experiments': [e for e in events if e.get('action') in ('patch', 'replace_source')],
                   'events': events, 'observations': investigation.get('observations', []),
                   'capability_tasks': investigation.get('capability_tasks', []),
                   'result': data.get('result', {})}
            reports.append(row)
            function_dir = target / data['config']['function']
            function_dir.mkdir(exist_ok=True)
            (function_dir / 'events.json').write_text(json.dumps(row, indent=2) + '\n')
            if (path.parent / 'result.best.c').is_file():
                shutil.copy2(path.parent / 'result.best.c', function_dir / 'best.c')
            campaign = path.parent / 'campaign-result.json'
            if campaign.exists():
                result = json.loads(campaign.read_text())
                selected = {key: result.get(key) for key in ('score', 'exact', 'calls_attempted',
                    'invalid_proposals', 'compiled_proposals', 'best_source_sha256', 'source_sha256')}
                selected['semantic_status'] = (result.get('semantic_validation') or {}).get('status')
                (function_dir / 'outcome.json').write_text(json.dumps(selected, indent=2) + '\n')
    for path in NATIVE.glob('notebooks/*.jsonl'):
        destination = OUTPUT / 'notebooks' / path.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(path, destination)
    for folder in NATIVE.glob('register-control*'):
        for path in folder.glob('*'):
            if path.is_file() and path.suffix in ('.c', '.json', '.jsonl', '.s'):
                destination = OUTPUT / folder.name / path.name
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(path, destination)
    index = [{key: row[key] for key in ('revision', 'function', 'native_receipt', 'actions', 'statuses')}
             for row in reports]
    (OUTPUT / 'index.json').write_text(json.dumps(index, indent=2) + '\n')
    print(json.dumps({'collected_runs': len(index), 'output': str(OUTPUT),
                      'latest': [row for row in index if row['revision'] == max(r['revision'] for r in index)]}, indent=2))


if __name__ == '__main__':
    main()

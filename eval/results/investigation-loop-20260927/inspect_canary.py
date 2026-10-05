import json
from pathlib import Path
ROOT = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
for path in sorted(ROOT.glob('**/model/*/result.repair.json')):
    data = json.loads(path.read_text())
    print(json.dumps({'path': str(path.relative_to(ROOT)), 'function': data['config']['function'], 'events': [
        {k: e.get(k) for k in ('step', 'action', 'status', 'error', 'hypothesis', 'score')}
        for e in data['investigation']['events']]}), flush=True)
for path in sorted((ROOT / 'model').glob('*/error.txt')):
    print(path, path.read_text()[-3000:])

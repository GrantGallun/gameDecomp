"""Freeze retained candidate inputs and the current experimental machinery."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import zlib

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/compiler-causal-20261002')


def main():
    WORK.mkdir(parents=True, exist_ok=False)
    code = WORK / 'code'
    for folder in ('solver', 'kb', 'miner', 'patterns', 'eval'):
        shutil.copytree(PROJECT / folder, code / folder,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', '*.sqlite', '*.pyc'))
    for filename in ('pyproject.toml',):
        if (PROJECT / filename).exists():
            shutil.copy2(PROJECT / filename, code / filename)
    protocol = json.loads((HERE / 'protocol.json').read_text())
    old_results = Path('/home/grant/decomp/runs/site-edits-20260929/results-r2.jsonl')
    retained = {r['function']: r for r in map(json.loads, old_results.read_text().splitlines())}
    native = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
    pointer = json.loads((native / 'campaign.json').read_text())
    research = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
    store = sqlite3.connect((native / pointer['store']).as_uri() + '?mode=ro', uri=True)
    manifest = json.loads(store.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0])
    inputs = []
    for scope in ('development', 'followup'):
        for name in protocol[scope]:
            source = retained[name]['source']
            path = WORK / 'inputs' / (name + '.c')
            path.parent.mkdir(exist_ok=True)
            path.write_text(source)
            node = json.loads(zlib.decompress(store.execute('SELECT payload FROM objects WHERE hash=?', (manifest['nodes'][name],)).fetchone()[0]))
            exact_count = research.execute('SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1', (name,)).fetchone()[0]
            inputs.append({'function': name, 'scope': scope, 'source': str(path),
                           'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'origin': str(old_results), 'campaign_status_at_freeze': node['status'],
                           'research_exact_attempts_at_freeze': exact_count,
                           'assistance': 'game headers and unknown earlier ancestry'})
    pins = {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in code.rglob('*') if p.is_file()}
    result = {'work': str(WORK), 'code': str(code), 'campaign_checkpoint': pointer['commit'],
              'inputs': inputs, 'code_pins': pins, 'training_eligible': False}
    (HERE / 'inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'code_pins'}, indent=2))


if __name__ == '__main__':
    main()

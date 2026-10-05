"""Capture completed native artifacts; run from WSL after smoke-03 succeeds."""
import json
from pathlib import Path
import shutil

source = Path('/home/grant/decomp/experiments/research-suite-20260928-smoke-03')
destination = Path(__file__).resolve().parent
smoke = json.loads((source / 'smoke.json').read_text())
if not smoke['passed']:
    raise SystemExit('refusing to package a failed smoke as a passing receipt')
rows = [json.loads(line) for path in source.rglob('attempts.jsonl')
        for line in path.read_text().splitlines()]
keys = json.loads((source / 'key-stress/audit.json').read_text())
line = json.loads((source / 'line-stress/audit.json').read_text())
summary = {'native_output': str(source), 'attempts': len(rows),
           'compiler_failures': sum(not row['compiled'] for row in rows),
           'certified_exact_attempts': sum(row['exact'] for row in rows),
           'compile_seconds': sum(row['seconds'] for row in rows),
           'key_stress': {k: keys[k] for k in ('compiles', 'key_calls', 'same_key_pairs', 'conclusive',
                                               'violations', 'unavailable', 'different_key_pairs')},
           'line_macro_stress': {k: line[k] for k in ('compiles', 'different_key_pairs', 'violations')},
           'scope': 'synthetic controls; not campaign success counts'}
shutil.copyfile(source / 'smoke.json', destination / 'native-smoke.json')
shutil.copyfile(source / 'key-stress/audit.json', destination / 'key-stress.json')
shutil.copyfile(source / 'line-stress/audit.json', destination / 'line-stress.json')
(destination / 'native-costs.json').write_text(json.dumps(summary, indent=2) + '\n')
shutil.make_archive(str(destination / 'native-smoke-artifacts'), 'gztar', root_dir=source)
print(json.dumps(summary, indent=2))

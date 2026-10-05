"""Execute the sealed prospective experiment once; stop on any failed stage."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
NATIVE = Path('/home/grant/decomp/experiments/compiler-effects-20260926')
receipt = HERE / 'pipeline.json'
if receipt.exists():
    raise RuntimeError('pipeline already started; inspect its receipt before any recovery')
rows = []
receipt.write_text(json.dumps({'status': 'running', 'stages': rows}, indent=2))
for stage in ('pin-code', 'run-dev', 'fit', 'prepare-eval', 'run-eval', 'report'):
    started = time.perf_counter()
    print(f'STAGE {stage}', flush=True)
    command = [sys.executable, str(HERE / 'benchmark.py'), stage]
    with (HERE / f'{stage}.log').open('x') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        for line in process.stdout:
            log.write(line)
            log.flush()
            print(line, end='', flush=True)
        code = process.wait()
    rows.append({'stage': stage, 'command': command, 'exit_code': code,
                 'elapsed_seconds': time.perf_counter() - started})
    receipt.write_text(json.dumps({'status': 'failed' if code else 'running', 'stages': rows}, indent=2))
    if code:
        raise SystemExit(code)
for name in ('manifest.json', 'code-pins.json', 'model.json', 'evaluation-rankings.json', 'report.json'):
    shutil.copy2(NATIVE / name, HERE / name)
    shutil.copy2(NATIVE / (name + '.sha256'), HERE / (name + '.sha256'))
receipt.write_text(json.dumps({'status': 'complete', 'stages': rows,
                              'report_sha256': hashlib.sha256((HERE / 'report.json').read_bytes()).hexdigest()}, indent=2))

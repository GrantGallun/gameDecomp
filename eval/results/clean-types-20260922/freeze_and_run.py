"""Keep the measured solver independent of unrelated concurrent workspace edits."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
NATIVE = Path.home()/'decomp/experiments/clean-types-20260922/code-frozen'
receipt = OUT/'frozen-code.json'
if not receipt.exists():
    copied = {}
    for directory in ('solver','eval','kb','oracle','tools','patterns','miner'):
        for folder,dirs,files in os.walk(ROOT/directory):
            dirs[:] = [d for d in dirs if d not in {'results','__pycache__','.git','.venv','node_modules','build'}]
            for name in files:
                path=Path(folder)/name
                relative = path.relative_to(ROOT)
                if path.suffix not in {'.py','.sql','.json'}:continue
                data = path.read_bytes()
                destination = NATIVE/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(data)
                copied[str(relative)] = hashlib.sha256(data).hexdigest()
    receipt.write_text(json.dumps(dict(root=str(NATIVE),files=copied),indent=2)+'\n')
snapshot = json.loads(receipt.read_text())
assert all(hashlib.sha256((NATIVE/path).read_bytes()).hexdigest()==sha for path,sha in snapshot['files'].items())
env = {**os.environ,'GAMEDECOMP_CODE_ROOT':str(NATIVE)}
print(f'Frozen {len(snapshot["files"])} code/data files at {NATIVE}',flush=True)
script = sys.argv[1] if len(sys.argv)>1 else 'replay_parallel.py'
assert script in {'replay_parallel.py','object_replay.py','confirm_exact.py','summarize.py','verify_focus.py'}
raise SystemExit(subprocess.call([sys.executable,str(OUT/script)],cwd=ROOT,env=env))

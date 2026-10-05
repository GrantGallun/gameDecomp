"""Complete the test-data snapshot; do not modify frozen implementation files."""
import hashlib
import json
from pathlib import Path
import shutil

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
CODE=Path(json.loads((OUT/'freeze.json').read_text())['code_root'])
source=ROOT/'tests/fixtures'
dest=CODE/'tests/fixtures'
shutil.copytree(source,dest,dirs_exist_ok=True)
files={p.relative_to(CODE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
       for p in sorted(dest.rglob('*')) if p.is_file()}
guided=Path('eval/results/uopt-trace-20260914/guided')
(CODE/guided).mkdir(parents=True,exist_ok=True)
for path in (ROOT/guided).glob('*.c'):
    target=CODE/guided/path.name
    shutil.copy2(path,target)
    files[target.relative_to(CODE).as_posix()]=hashlib.sha256(target.read_bytes()).hexdigest()
(OUT/'test-fixtures.json').write_text(json.dumps(files,indent=2))
print(json.dumps({'test_fixture_files':len(files)}))

"""Snapshot current observer code and existing test assets into native WSL."""
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
CODE = Path('/home/grant/decomp/experiments/generated-potential-20260922/code-v1')
PREVIOUS = Path('/home/grant/decomp/experiments/capability-envelope-20260922/code-v1')


if __name__ == '__main__':
    if (OUT/'analysis').exists():
        raise RuntimeError('existing result freezes the code; use a new revision directory')
    if not CODE.exists():
        shutil.copytree(PREVIOUS, CODE)
    for folder in ('solver', 'eval', 'kb', 'tests'):
        for path in (ROOT/folder).glob('*.py'):
            shutil.copy2(path, CODE/folder/path.name)
    files = {p.relative_to(CODE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ('solver', 'eval', 'kb', 'tests') for p in sorted((CODE/folder).glob('*.py'))}
    fixtures = json.loads((OUT.parent/'capability-envelope-20260922/test-fixtures.json').read_text())
    for path, expected in fixtures.items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        assert hashlib.sha256((CODE/path).read_bytes()).hexdigest() == expected, path
    result = {'code_root': str(CODE), 'files': files, 'test_fixtures': fixtures,
              'new_compiler_calls': 0, 'training_eligible': False}
    (OUT/'freeze.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'code_files': len(files), 'test_fixtures': len(fixtures), 'code_root': str(CODE)}))

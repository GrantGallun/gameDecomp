"""Freeze the observer and its contract references; no compiler work."""
import hashlib
import json
from pathlib import Path
import shutil

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
CODE=Path('/home/grant/decomp/experiments/capability-envelope-20260922/code-v1')
PREVIOUS=Path('/home/grant/decomp/experiments/repair-theory-20260922/code-v1')


def main():
    if (OUT/'analysis').exists():raise RuntimeError('analysis already exists; freeze is immutable')
    if not CODE.exists():shutil.copytree(PREVIOUS,CODE)
    # Preserve runtime dependencies in the prior snapshot, then pin current
    # modules and tests. Each referenced owner/test lives in the native snapshot.
    for folder in ('solver','eval','kb','tests'):
        (CODE/folder).mkdir(exist_ok=True)
        for path in (ROOT/folder).glob('*.py'):
            shutil.copy2(path,CODE/folder/path.name)
    files={p.relative_to(CODE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for folder in ('solver','eval','kb','tests') for p in sorted((CODE/folder).glob('*.py'))}
    manifest={'code_root':str(CODE),'files':files,'new_compiler_calls':0,
              'input_report':'../repair-theory-20260922/paired/report.json','training_eligible':False}
    (OUT/'freeze.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'files':len(files),'code_root':str(CODE)}))


if __name__=='__main__':main()

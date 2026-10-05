"""Run the frozen capability observer against all prior paired receipts."""
import hashlib
import json
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
frozen=json.loads((OUT/'freeze.json').read_text())
CODE=Path(frozen['code_root'])
sys.path.insert(0,str(CODE))
from eval.capability_map import analyze


def check():
    for path,sha in frozen['files'].items():
        assert hashlib.sha256((CODE/path).read_bytes()).hexdigest()==sha,path


if __name__=='__main__':
    check()
    result=analyze((OUT/frozen['input_report']).resolve(),OUT/'analysis')
    check()
    print(json.dumps({k:result[k] for k in ('complete','new_compiler_calls','observations_analyzed','worlds_analyzed','status_counts')},indent=2))

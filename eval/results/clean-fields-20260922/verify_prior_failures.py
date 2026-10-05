"""Distinguish new regression identities from host-only failures on prior code."""
import contextlib
import json
import sys
from pathlib import Path
import pytest

OUT=Path(__file__).resolve().parent
root=Path.home()/'decomp/experiments/clean-types-20260922/code-frozen'
sys.path.insert(0,str(root))
from eval import training_control
results=[]
class Reporter:
    def pytest_runtest_logreport(self,report):
        if report.failed:
            results.append(dict(test=report.nodeid,phase=report.when,failure=str(report.longrepr)))
selected=[x[0] for x in json.loads((OUT/'tests-comparison.json').read_text())['new_failures']]
assert selected
with (OUT/'tests-prior-recheck.log').open('w') as stream:
    with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
        code=pytest.main(['-q','--tb=short',*selected],plugins=[Reporter()])
receipt=dict(implementation=str(root),training_control_module=training_control.__file__,
    exit_code=int(code),failures=results)
assert Path(training_control.__file__).is_relative_to(root)
(OUT/'tests-prior-recheck.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(dict(implementation=receipt['training_control_module'],
    failed=[(r['test'],r['phase']) for r in results])))

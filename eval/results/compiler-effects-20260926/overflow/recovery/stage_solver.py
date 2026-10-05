"""Test a native-WSL copy of frozen project with only the solver fix overlaid."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from inspect_checkpoint import FROZEN, sha
from apply_recovery import HERE, MAIN, NEW_SOLVER_SHA, EXPECTED_OLD_SOLVER

PY = Path('/home/grant/decomp/sbk1/.venv/bin/python')
SCRATCH = Path('/home/grant/decomp/experiments')


def main() -> None:
    source = MAIN / 'solver/mips_differential.py'
    old = FROZEN / 'solver/mips_differential.py'
    if sha(old) != EXPECTED_OLD_SOLVER or sha(source) != NEW_SOLVER_SHA:
        raise RuntimeError('one-file source/baseline changed')
    if not PY.is_file() or not SCRATCH.is_dir():
        raise RuntimeError('missing native WSL test environment')
    runs = []
    with tempfile.TemporaryDirectory(prefix='overflow-solver-stage-', dir=SCRATCH) as temporary:
        project = Path(temporary) / 'project'
        shutil.copytree(FROZEN, project,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        shutil.copy2(source, project / 'solver/mips_differential.py')
        if sha(project / 'solver/mips_differential.py') != NEW_SOLVER_SHA:
            raise RuntimeError('staged overlay hash differs')
        env = dict(os.environ, PYTHONPATH=str(project))
        for label, test in (
                ('frozen', project / 'tests/test_mips_differential.py'),
                ('main-regression', MAIN / 'tests/test_mips_differential.py')):
            command = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', str(test)]
            result = subprocess.run(command, cwd=project, env=env,
                                    capture_output=True, text=True, timeout=300)
            runs.append({'label': label, 'command': command, 'returncode': result.returncode,
                         'output': result.stdout + result.stderr})
        probe = subprocess.run([str(PY), '-c',
                                'from solver import mips_differential; print(mips_differential.__file__)'],
                               cwd=project, env=env, capture_output=True, text=True, timeout=30)
        if probe.returncode or Path(probe.stdout.strip()).resolve() != (project / 'solver/mips_differential.py').resolve():
            raise RuntimeError('stage test imported a nonstaged solver')
    receipt = {'kind': 'overflow-solver-stage-v1',
               'old_sha256': EXPECTED_OLD_SOLVER, 'new_sha256': NEW_SOLVER_SHA,
               'main_test_sha256': sha(MAIN / 'tests/test_mips_differential.py'),
               'runs': runs, 'returncode': max(row['returncode'] for row in runs),
               'passed': all(row['returncode'] == 0 for row in runs)}
    path = HERE / 'solver-stage.json'
    if path.exists():
        raise RuntimeError('staged test receipt already exists; inspect before rerun')
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'passed': receipt['passed'],
                      'runs': [{'label': r['label'], 'returncode': r['returncode'],
                                'tail': r['output'].strip().splitlines()[-3:]}
                               for r in runs]}, indent=2))
    if not receipt['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

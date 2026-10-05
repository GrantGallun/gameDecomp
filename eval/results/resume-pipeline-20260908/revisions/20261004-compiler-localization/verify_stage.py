"""Scoped active-header object-conflict amendment; preserves all retained nodes.
Use stage.py, verify_stage.py, then apply_amendment.py --apply after a drained pause.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import stage

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
MAIN = HERE.parents[4]
WORK = Path('/home/grant/decomp/experiments/compiler-localization-stage-20261004')
PY = Path('/home/grant/decomp/sbk1/.venv/bin/python')
# Regalloc tests read these fixed fixtures (test inputs only, never deployed); same list as 20260926.
FIXTURES = ('tools/context_closure.py',)
# Failing in the untouched frozen tree before this amendment. Deselected from the overlay run, and
# re-run on a pristine frozen copy, where each MUST fail: a deselection cannot hide a new regression.
PREEXISTING_FAILURES: dict = {
    'tests/test_completion_campaign.py::test_forked_intake_keeps_failed_context_and_correct_parent':
        'fails on the untouched frozen tree (parents [42, 42, 43] vs [42, 43]); intake, not touched here',
}


def main() -> None:
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest['frozen_project'] != str(FROZEN):
        raise RuntimeError('wrong frozen project')
    if set(manifest['changed']) != set(stage.CHANGED) or set(manifest['tests']) != set(stage.TESTS):
        raise RuntimeError('stage file/test roster differs from reviewed scope')
    for rel, row in manifest['changed'].items():
        if hashlib.sha256((HERE / 'staged' / rel).read_bytes()).hexdigest() != row['new_sha256']:
            raise RuntimeError(f'staged source changed after manifest: {rel}')
    for rel, digest in manifest['tests'].items():
        if hashlib.sha256((HERE / 'staged' / rel).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'staged test changed after manifest: {rel}')
    WORK.mkdir(parents=True, exist_ok=True)
    project = WORK / 'project'
    if project.exists():
        if project.resolve() != (WORK / 'project').resolve() or not project.is_relative_to(WORK):
            raise RuntimeError('scratch path escaped native work directory')
        shutil.rmtree(project)
    shutil.copytree(FROZEN, project, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for rel in list(manifest['changed']) + list(manifest['tests']) + list(manifest.get('test_fixtures', {})):
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / 'staged' / rel, target)
    fixture_hashes = {}
    for rel in FIXTURES:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MAIN / rel, target)
        fixture_hashes[rel] = hashlib.sha256((MAIN / rel).read_bytes()).hexdigest()
    missing = [rel for rel in stage.FROZEN_TESTS if not (project / rel).is_file()]
    if missing:
        raise RuntimeError(f'frozen caller tests missing: {missing}')
    env = dict(os.environ, PYTHONPATH=str(project))
    deselect = [arg for test in PREEXISTING_FAILURES for arg in ('--deselect', test)]
    command = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', str(WORK / 'pytest-tmp'),
               *deselect, *stage.TESTS, *stage.FROZEN_TESTS]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True,
                            timeout=3600, check=False)
    (HERE / 'stage-test.log').write_text(result.stdout + result.stderr)
    # The deselected tests must fail WITHOUT this amendment too.
    pristine = WORK / 'pristine'
    if pristine.exists():
        if pristine.resolve() != (WORK / 'pristine').resolve():
            raise RuntimeError('scratch path escaped native work directory')
        shutil.rmtree(pristine)
    shutil.copytree(FROZEN, pristine, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for rel in FIXTURES:
        (pristine / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MAIN / rel, pristine / rel)
    baseline = {}
    for test in PREEXISTING_FAILURES:
        run = subprocess.run([str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                              '--basetemp', str(WORK / 'pytest-pristine'), test],
                             cwd=pristine, env=dict(os.environ, PYTHONPATH=str(pristine)),
                             capture_output=True, text=True, timeout=600, check=False)
        baseline[test] = {'fails_without_amendment': run.returncode != 0,
                          'reason': PREEXISTING_FAILURES[test],
                          'tail': run.stdout.strip().splitlines()[-1:]}
    preexisting_ok = all(row['fails_without_amendment'] for row in baseline.values())
    receipt = {'kind': 'staged-frozen-package-tests',
               'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'dry': manifest['dry'],
               'project': str(project), 'command': command, 'test_only_fixture_hashes': fixture_hashes,
               'preexisting_failures': baseline,
               'returncode': result.returncode, 'tail': result.stdout.strip().splitlines()[-4:],
               'passed': result.returncode == 0 and preexisting_ok}
    (HERE / 'stage-test.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: receipt[k] for k in ('dry', 'returncode', 'tail', 'preexisting_failures', 'passed')},
                     indent=2))
    if not receipt['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

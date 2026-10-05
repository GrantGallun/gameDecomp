"""Test a real copied frozen package tree with only stage.json overlays.

All build/test scratch remains on native WSL storage. No live frozen or native
campaign file is changed. Re-run whenever stage.py changes its manifest.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
MAIN = HERE.parents[4]
WORK = Path('/home/grant/decomp/experiments/delivery-stage-20260926')
PY = Path('/home/grant/decomp/sbk1/.venv/bin/python')
TESTS = (
    'tests/test_binary_type_identity.py',
    'tests/test_binary_type_context.py',
    'tests/test_binary_type_draft.py',
    'tests/test_binary_type_campaign.py',
    'tests/test_campaign_fast.py',
    'tests/test_completion_campaign.py',
    'tests/test_regalloc_mutations.py',
    'tests/test_m2c_placeholders.py',
    'tests/test_repair_queue.py',
)
GUIDED_NAMES = (
    'updateRaceUiScorePopupSlideIn', 'updateRaceSetupNamePlateSlideIn',
    'updateRaceUiCrashScorePopupSlideIn', 'updateRaceUiTrickScorePopupSlideIn',
    'updateTimeTrialRecordDeltaPopupSlideIn', 'updateCharacterSelectRosterIcons',
)


def main() -> None:
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest['frozen_project'] != str(FROZEN):
        raise RuntimeError('wrong frozen project')
    WORK.mkdir(parents=True, exist_ok=True)
    project = WORK / 'project'
    if project.exists():
        # Fixed, verified native scratch target; never delete a computed path.
        if project.resolve() != (WORK / 'project').resolve() or not project.is_relative_to(WORK):
            raise RuntimeError('scratch path escaped native work directory')
        shutil.rmtree(project)
    shutil.copytree(FROZEN, project, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for rel in list(manifest['changed']) + list(manifest['tests']):
        source = HERE / 'staged' / rel
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Existing mutation tests read these fixed experiment fixtures. They are
    # test inputs only and never enter the frozen campaign or deployed pins.
    guided = Path('eval/results/uopt-trace-20260914/guided')
    fixture_hashes = {}
    for name in GUIDED_NAMES:
        for suffix in ('before', 'exact'):
            rel = guided / f'{name}.{suffix}.c'
            source = MAIN / rel
            target = project / rel
            if not source.is_file():
                raise RuntimeError(f'missing immutable test fixture: {source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            fixture_hashes[str(rel)] = hashlib.sha256(source.read_bytes()).hexdigest()
    env = dict(os.environ, PYTHONPATH=str(project))
    command = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp', str(WORK / 'pytest-tmp'), *TESTS]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True,
                            timeout=3600, check=False)
    (HERE / 'stage-test.log').write_text(result.stdout + result.stderr)
    receipt = {'kind':'staged-frozen-package-tests',
               'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
               'project':str(project), 'command':command,
               'test_only_fixture_hashes':fixture_hashes,
               'returncode':result.returncode, 'tail':result.stdout.strip().splitlines()[-4:],
               'passed':result.returncode == 0}
    (HERE / 'stage-test.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()

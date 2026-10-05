"""Exercise staged sources in a copied frozen package on native WSL storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import stage

HERE = stage.HERE
WORK = Path('/home/grant/decomp/experiments/combined-frontier-deployment-20260926')
PY = Path('/home/grant/decomp/sbk1/.venv/bin/python')
GUIDED_NAMES = (
    'updateRaceUiScorePopupSlideIn', 'updateRaceSetupNamePlateSlideIn',
    'updateRaceUiCrashScorePopupSlideIn', 'updateRaceUiTrickScorePopupSlideIn',
    'updateTimeTrialRecordDeltaPopupSlideIn', 'updateCharacterSelectRosterIcons',
)


def main() -> None:
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest['frozen_project'] != str(stage.FROZEN) or
            set(manifest['changed']) != set(stage.CHANGED) or
            set(manifest['tests']) != set(stage.TESTS) or
            set(manifest['test_only_fixtures']) != set(stage.FIXTURES)):
        raise RuntimeError('staged scope differs from reviewed scope')
    payload = {**{rel: row['new_sha256'] for rel, row in manifest['changed'].items()},
               **manifest['tests'], **manifest['test_only_fixtures']}
    for rel, expected in payload.items():
        if stage.sha(stage.STAGED / rel) != expected:
            raise RuntimeError(f'staged file hash mismatch: {rel}')
    WORK.mkdir(parents=True, exist_ok=True)
    project = WORK / 'project'
    if project.exists():
        if not project.is_dir() or project.is_symlink() or project.resolve() != (WORK / 'project').resolve():
            raise RuntimeError('unsafe existing test scratch')
        shutil.rmtree(project)
    shutil.copytree(stage.FROZEN, project,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for rel in payload:
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage.STAGED / rel, target)
    guided_hashes = {}
    for name in GUIDED_NAMES:
        for suffix in ('before', 'exact'):
            rel = Path('eval/results/uopt-trace-20260914/guided') / f'{name}.{suffix}.c'
            source, target = stage.MAIN / rel, project / rel
            if not source.is_file():
                raise RuntimeError(f'missing test fixture: {rel}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            guided_hashes[str(rel)] = stage.sha(source)
    env = dict(os.environ, PYTHONPATH=str(project))
    command = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp', str(WORK / 'pytest-tmp'), *stage.TESTS]
    result = subprocess.run(command, cwd=project, env=env,
                            capture_output=True, text=True, timeout=3600, check=False)
    (HERE / 'stage-test.log').write_text(result.stdout + result.stderr)
    receipt = {'kind': 'copied-frozen-package-tests',
               'manifest_sha256': stage.digest_bytes(manifest_bytes),
               'project': str(project), 'command': command,
               'test_only_fixture_hashes': guided_hashes,
               'returncode': result.returncode,
               'tail': (result.stdout + result.stderr).strip().splitlines()[-6:],
               'passed': result.returncode == 0}
    (HERE / 'stage-test.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()

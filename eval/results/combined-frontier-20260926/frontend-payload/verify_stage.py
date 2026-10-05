"""Test exact staged sources in a copied frozen package on native WSL storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import stage

HERE = stage.HERE
WORK = Path('/home/grant/decomp/experiments/combined-frontier-frontend-20260926')
PY = Path('/home/grant/decomp/sbk1/.venv/bin/python')


def main() -> None:
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    payload = json.loads((HERE / 'manifest.json').read_bytes())
    if (manifest['kind'] != 'unapplied-combined-frontier-frontend' or
            manifest['payload_manifest_sha256'] != stage.sha(HERE / 'manifest.json') or
            set(manifest['changed']) != set(payload['changed']) or
            set(manifest['tests']) != set(stage.TESTS)):
        raise RuntimeError('staged scope or payload identity differs')
    for rel, row in manifest['changed'].items():
        if stage.sha(stage.STAGED / rel) != row['new_sha256']:
            raise RuntimeError(f'staged code drift: {rel}')
    for rel, expected in manifest['tests'].items():
        if stage.sha(stage.STAGED / rel) != expected:
            raise RuntimeError(f'staged test drift: {rel}')
    if stage.sha(stage.FROZEN / stage.FROZEN_TEST) != manifest['frozen_test_sha256']:
        raise RuntimeError('frozen placeholder guard drift')
    WORK.mkdir(parents=True, exist_ok=True)
    project = WORK / 'project'
    if project.exists():
        if not project.is_dir() or project.is_symlink() or project.resolve() != (WORK / 'project').resolve():
            raise RuntimeError('unsafe existing test scratch')
        shutil.rmtree(project)
    shutil.copytree(stage.FROZEN, project,
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for rel in list(manifest['changed']) + list(manifest['tests']):
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stage.STAGED / rel, target)
    original = (stage.FROZEN / 'solver/compile_recovery.py').read_text()
    merged = (project / 'solver/compile_recovery.py').read_text()
    first = '    # FIRST STAGE, because a file cfe refuses to parse'
    later = '    from solver import wide_parameter_repair, frontend_repair'
    retained = original[original.index(first):original.index(later, original.index(first))]
    if retained not in merged or merged.count('m2c_placeholders.rewrite(source)') != 1:
        raise RuntimeError('frozen placeholder recovery path changed')
    env = dict(os.environ, PYTHONPATH=str(project))
    command = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
               '--basetemp', str(WORK / 'pytest-tmp'), *stage.TESTS, stage.FROZEN_TEST]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True,
                            text=True, timeout=3600, check=False)
    (HERE / 'stage-test.log').write_text(result.stdout + result.stderr)
    receipt = {'kind': 'copied-frozen-frontend-tests',
               'manifest_sha256': stage.digest_bytes(manifest_bytes),
               'project': str(project), 'command': command,
               'returncode': result.returncode,
               'tail': (result.stdout + result.stderr).strip().splitlines()[-6:],
               'passed': result.returncode == 0}
    (HERE / 'stage-test.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()

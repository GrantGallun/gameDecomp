"""Run focused tests with the staged solver modules over the frozen package."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
FROZEN = PROJECT / 'eval/results/resume-pipeline-20260908/code'
TESTS = [
    'tests/test_global_scalar_view.py',
    'tests/test_global_field_view.py',
    'tests/test_stack_scalar_arrays.py',
    'tests/test_header_signature_view.py',
    'tests/test_call_arity_repair.py',
    'tests/test_modelrepair_frontend_routes.py',
    'tests/test_frontend_fixits.py',
    'tests/test_frontend_full_diagnostics.py',
    'tests/test_intake_self_header.py',
    'tests/test_header_alias_recovery.py',
    'tests/test_modelrepair.py',
    'tests/test_opaque_declarations.py',
    'tests/test_compile_obligations_alias.py',
    'tests/test_typedecl.py',
    'tests/test_void_field_repair.py',
]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='frozen-frontend-overlay-', dir=HERE) as directory:
        overlay = Path(directory)
        if not overlay.resolve().is_relative_to(HERE.resolve()):
            raise RuntimeError('test overlay escaped the staged workspace')
        shutil.copytree(FROZEN, overlay, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for source in (HERE / 'staged').rglob('*.py'):
            target = overlay / source.relative_to(HERE / 'staged')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        tests = [str(PROJECT / path) for path in TESTS]
        tests.append(str(overlay / 'tests/test_compile_recovery_placeholder.py'))
        code = (
            'import sys, pytest; '
            'sys.path.insert(0, sys.argv[1]); '
            'raise SystemExit(pytest.main(sys.argv[2:]))'
        )
        command = [sys.executable, '-c', code, str(overlay), '-q', '-p', 'no:cacheprovider',
                   '--junitxml=' + str(HERE / 'focused-tests.xml'), *tests]
        return subprocess.call(command, cwd=PROJECT)


if __name__ == '__main__':
    raise SystemExit(main())

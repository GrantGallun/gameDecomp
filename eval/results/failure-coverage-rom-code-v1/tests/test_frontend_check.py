import json
from types import SimpleNamespace

import pytest

from solver import frontend_check as frontend


MAKEFILE = '''WERROR ?= 0
CC_CHECK = clang
CC_CHECK_FLAGS = -fsyntax-only -std=gnu89
CC_CHECK_WARNINGS = -Werror=incompatible-function-pointer-types
CC_CHECK_INCLUDES = -Iinclude
C_DEFINES = -DLANGUAGE_C
CC_CHECK_MIPS_DEFINES = -DMIPSEB
ifneq ($(WERROR),0)
CC_CHECK_WARNINGS += -Werror
endif
danger:
\tnever-run-this
'''


def test_projection_keeps_selected_errors_without_executing_recipes():
    projected = frontend.projection(MAKEFILE, 'build/src/foo.o')
    assert '-Werror=incompatible-function-pointer-types' in projected
    assert '+= -Werror' not in projected
    assert 'never-run-this' not in projected
    assert '__DECOMP_CC_CHECK__' in projected


@pytest.mark.parametrize('change', [
    lambda s: s.replace('WERROR ?= 0', 'WERROR ?= 1'),
    lambda s: s + '\nifdef OTHER\nCC_CHECK_FLAGS += -Werror\nendif\n',
    lambda s: s.replace('-std=gnu89', '$(shell malicious)'),
])
def test_projection_refuses_unknown_policy_or_active_expansion(change):
    with pytest.raises(ValueError):
        frontend.projection(change(MAKEFILE), 'build/src/foo.o')


def test_checker_converts_then_checks_and_binds_diagnostics(tmp_path, monkeypatch):
    (tmp_path / 'Makefile').write_text(MAKEFILE)
    source = tmp_path / 'input.c'
    source.write_text('void f(void) {}')
    monkeypatch.setattr(frontend, 'recipe', lambda *a: {'command': ['clang', '-fsyntax-only']})
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            assert command[:3] == ['python3', 'tools/textconv.py', 'tools/charmap.txt']
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        return SimpleNamespace(returncode=1, stdout='', stderr=f'{command[-1]}: error: wrong callback')
    monkeypatch.setattr(frontend.subprocess, 'run', run)
    report = frontend.check(tmp_path, source, 'build/src/foo.o')
    assert report['passed'] is False
    assert report['diagnostics'] == 'candidate.c: error: wrong callback'
    assert report['source_sha256'] == frontend.compiler_recipe.sha(source.read_bytes())
    assert json.loads(source.with_suffix('.frontend.json').read_text()) == report


def test_missing_checker_is_unavailable_not_pass(tmp_path, monkeypatch):
    (tmp_path / 'Makefile').write_text(MAKEFILE)
    source = tmp_path / 'input.c'
    source.write_text('void f(void) {}')
    def unavailable(*args):
        raise ValueError('project C checker unavailable')
    monkeypatch.setattr(frontend, 'recipe', unavailable)
    assert frontend.check(tmp_path, source, 'build/src/foo.o')['passed'] is None

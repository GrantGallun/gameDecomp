import ctypes
import os
import re
import shutil
import subprocess

import pytest

from solver.inline_expansion import candidates


def test_normal_differential_search_reaches_expansion_with_bound():
    from eval import differential_repair_pilot as pilot
    source = 'int h(int x) { return x + 3; } int f(int a) { return h(a) + h(4); }'
    rows = pilot.deterministic_exactness_candidates(source, 'f', '-jal h\n+addiu v0,a0,3', 1)
    assert len(rows) == 1
    assert rows[0] == candidates(source, 'f', 1)[0]
    assert pilot.deterministic_exactness_candidates(source, 'f', '-jal h\n+addiu v0,a0,3', 0) == ()


def test_fires_for_source_local_helper_and_preserves_definition():
    helper = 'static int mix(int value, int mask) { return (value << 2) ^ mask; }\n'
    caller = 'int caller(int a, int b) { return mix(a, b); }'
    result = candidates(helper + caller, 'caller')
    assert len(result) == 1
    assert result[0].label.startswith('inline-expansion:mix@')
    assert result[0].source == helper + 'int caller(int a, int b) { return ((int)((((int)(a)) << 2) ^ ((int)(b)))); }'
    assert not candidates(caller, 'caller')  # Never load an external helper.


def test_one_site_per_variant_source_order_and_hard_budget():
    source = 'int h(int x) { return x + 3; } int f(int a) { return h(a) + h(4) + h(5); }'
    rows = candidates(source, 'f')
    assert len(rows) == 3
    assert all(row.source.count('h(') == source.count('h(') - 1 for row in rows)
    assert len({row.source for row in rows}) == 3
    assert candidates(source, 'f', 2) == rows[:2]
    assert candidates(source, 'f', 0) == ()
    assert candidates(source, 'missing') == ()


@pytest.mark.parametrize('expression', [
    'x + x', 'x++', '++x', '--x', 'x = 2', '(x, 3)',
    'other(x)', 'h(x)', '*x', '&x', 'x[0]', 'x.member',
    'x ? 1 : 2', 'x && 1', 'x || 1', 'global + x', 'sizeof(x)',
    '(float)x', '"x"', '0x1p2', '0.5',
])
def test_helper_effects_control_flow_unknown_bindings_and_duplication_decline(expression):
    source = 'int h(int x) { return ' + expression + '; } int f(int a) { return h(a); }'
    assert candidates(source, 'f') == ()


@pytest.mark.parametrize('actual', ['a++', '++a', '*p', '&a', 'p[0]', 'other(a)', 'a + 1', 'GLOBAL'])
def test_nontrivial_actuals_decline(actual):
    source = 'int h(int x) { return x + 1; } int f(int a, int *p) { return h(' + actual + '); }'
    assert candidates(source, 'f') == ()


@pytest.mark.parametrize('prefix,body', [
    ('#define h(x) (x)\n', 'return h(a);'),
    ('#if ENABLED\n', 'return h(a);'),
    ('#include HEADER\n', 'return h(a);'),
    ('', 'volatile int v; return h(a);'),
    ('', 'int a; return h(a);'),
    ('', 'int (*h)(int); return h(a);'),
    ('', 'int h; return h(a);'),
    ('', 'return object.h(a);'),
])
def test_macro_volatile_and_shadowing_decline(prefix, body):
    source = prefix + 'int h(int x) { return x; } int f(int a) { ' + body + ' }'
    assert candidates(source, 'f') == ()


def test_literal_includes_allowed_without_guessing_external_types():
    source = '#include "common.h"\nint h(int x) { return x + 1; } int f(int a) { return h(a); }'
    assert len(candidates(source, 'f')) == 1
    assert candidates(source.replace('int h', 's32 h'), 'f') == ()


def test_casts_preserve_narrow_parameter_and_result_conversions():
    source = 'static signed char h(unsigned char x) { return x + 200; } int f(int a) { return h(a); }'
    result, = candidates(source, 'f')
    assert 'return ((signed char)(((unsigned char)(a)) + 200));' in result.source


def test_substitution_is_simultaneous_and_cannot_capture_other_parameter_names():
    source = 'int h(int x, int y) { return x - y; } int f(int y, int x) { return h(y, x); }'
    result, = candidates(source, 'f')
    assert 'return ((int)(((int)(y)) - ((int)(x))));' in result.source


def test_comments_literals_and_unrelated_functions_are_preserved():
    source = 'int h(int x) { return x + 1; } int other(int x) { return h(x); }\n'
    source += 'int f(int a) { /* h(a) */ char *s="h(a) /* text */"; return h(a); }'
    result, = candidates(source, 'f')
    assert 'int other(int x) { return h(x); }' in result.source
    assert '/* h(a) */ char *s="h(a) /* text */";' in result.source


def test_ambiguous_definitions_and_non_expression_helpers_decline():
    assert not candidates('int h(int x) { return x; } int h(int x) { return x+1; } int f(int x) { return h(x); }', 'f')
    assert not candidates('int h(int x) { int y; y=x; return y; } int f(int x) { return h(x); }', 'f')
    assert not candidates('int h(int x) { if(x) return 1; return 0; } int f(int x) { return h(x); }', 'f')


@pytest.mark.parametrize('optimization', ['-O0', '-O2'])
def test_actual_c89_compiler_preserves_parameter_and_return_conversions(tmp_path, optimization):
    clang = shutil.which('clang')
    if not clang or (os.name == 'nt' and not shutil.which('lld-link')):
        pytest.skip('native clang/linker unavailable')
    source = 'static signed char h(unsigned char x) { return x + 200; } int f(int a) { return h(a); }'
    result, = candidates(source, 'f')
    unit = '\n'.join(re.sub(r'\b(h|f)\b', lambda m: m[0] + str(i), text)
                     for i, text in enumerate((source, result.source)))
    src = tmp_path / 'variants.c'
    src.write_text(unit)
    library = tmp_path / ('variants.dll' if os.name == 'nt' else 'variants.so')
    if os.name == 'nt':
        obj = tmp_path / 'variants.obj'
        subprocess.run([clang, '-std=c89', optimization, '-c', str(src), '-o', str(obj)],
                       check=True, capture_output=True)
        subprocess.run([shutil.which('lld-link'), '/dll', '/noentry', '/nodefaultlib',
                        '/out:' + str(library), '/export:f0', '/export:f1', str(obj)],
                       check=True, capture_output=True)
    else:
        subprocess.run([clang, '-std=c89', optimization, '-shared', '-fPIC', str(src),
                        '-o', str(library)], check=True, capture_output=True)
    loaded = ctypes.CDLL(str(library))
    for fn in (loaded.f0, loaded.f1):
        fn.argtypes = [ctypes.c_int]
        fn.restype = ctypes.c_int
    for value in list(range(-600, 601)) + [-(2**31), 2**31-1]:
        assert loaded.f0(value) == loaded.f1(value)

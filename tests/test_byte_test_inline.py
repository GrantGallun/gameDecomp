import pytest

from solver.byte_test_inline import candidates


SOURCE = '''#include "common.h"
int scan(u8 *input) {
    u8 *cursor;
    u8 value;
    cursor = input;
    if (*input != 0) {
        for (;;) {
            cursor += 1; value = *cursor;
            if (!(value != 0)) break;
        }
    }
    return cursor - input;
}
'''


def test_fires_on_split_byte_load_immediately_tested_by_loop():
    rows = candidates(SOURCE, 'scan')
    assert len(rows) == 1
    assert rows[0].label == 'inline-split-byte-zero-test:value'
    assert 'u8 value;' not in rows[0].source
    assert 'value = ' not in rows[0].source
    assert 'if (!((*cursor) != 0)) break;' in rows[0].source
    assert 'cursor += 1;' in rows[0].source
    assert candidates(rows[0].source, 'scan') == ()


@pytest.mark.parametrize('replacement', ['if (!value)', 'if (value == 0)', 'if (value != 0)'])
def test_supported_immediate_zero_tests(replacement):
    assert candidates(SOURCE.replace('if (!(value != 0))', replacement), 'scan')


@pytest.mark.parametrize('old,new', [
    ('u8 value;', 'volatile u8 value;'),
    ('u8 *cursor;', 'volatile u8 *cursor;'),
    ('u8 value;', 's8 value;'),
    ('value = *cursor;', 'value = *cursor++;'),
    ('value = *cursor;', 'value = load(cursor);'),
    ('if (!(value != 0))', 'if (other && !(value != 0))'),
    ('if (!(value != 0))', 'cursor++; if (!(value != 0))'),
    ('return cursor - input;', 'return value;'),
    ('cursor = input;', 'take(&(value)); cursor = input;'),
    ('cursor = input;', '{ u8 *cursor; } cursor = input;'),
    ('cursor = input;', '{ Other *cursor; } cursor = input;'),
    ('#include "common.h"', '#define value hidden'),
    ('u8 value;', 'u8 value = 0;'),
    ('u8 value;', 'u8 value, other;'),
])
def test_declines_uncertain_or_changed_semantics(old,new):
    assert candidates(SOURCE.replace(old,new), 'scan') == ()


def test_bound_and_missing_function():
    assert candidates(SOURCE, 'scan', 0) == ()
    assert candidates(SOURCE, 'absent') == ()
    assert len(candidates(SOURCE, 'scan', 1)) == 1


def test_comments_do_not_add_uses_or_directives():
    assert candidates(SOURCE.replace('u8 value;', 'u8 value; /* value #define */'), 'scan')


def test_pointer_name_containing_if_does_not_confuse_branch_boundary():
    row, = candidates(SOURCE.replace('cursor', 'ifoo'), 'scan')
    assert 'if (!((*ifoo) != 0)) break;' in row.source


def test_bounded_existing_register_family_routes_new_generator():
    from solver.principle_variants import isolated_register_web
    rows = isolated_register_web(SOURCE, 'scan', max_variants=32)
    assert any(row.label == 'inline-split-byte-zero-test:value' for row in rows)
    assert len(isolated_register_web(SOURCE, 'scan', max_variants=1)) == 1


def test_catalog_cites_actual_generator_compiler_replay():
    from patterns.catalog import CATALOG
    pattern = CATALOG['split-byte-zero-test-load']
    assert not pattern.is_hypothesis
    assert 'strlen-final-generator/summary.json' in pattern.confirmed_on[0]

"""A stored narrow increment must be reread, without stale locals or unsafe motion."""
from itertools import islice
import pytest


RANDOM = """u8 randomNextObject(void *p) {
    u8 idx;
    u8 value;
    value = (*(u8 *)((u8 *)(p) + 0x518)) + 1;
    (*(u8 *)((u8 *)(p) + 0x518)) = value;
    idx = value & 0xFF;
    return *(&table + idx);
}
"""
IDLE = """void updateEndingCreditsIdleSparkle(void *p) {
    u16 value;
    (*(u16 *)((unsigned char *)p + 0x1E)) += 1;
    if (((*(u16 *)((unsigned char *)p + 0x1E)) & 0xFFFF) == 4) {
        value = (*(u16 *)((unsigned char *)p + 0x1C)) + 1;
        (*(s16 *)((unsigned char *)p + 0x1E)) = 0U;
        (*(s16 *)((unsigned char *)p + 0x1C)) = value;
        if ((value & 0xFFFF) == 5) {
            (*(s16 *)((unsigned char *)p + 0x1C)) = 0U;
        }
    }
    consume(p);
}
"""
SIGNED = """void f(void *p) {
    s16 narrow;
    s32 comparison;

    narrow = (*(u16 *)((u8 *)(p) + 0x2A)) + 1;
    comparison = narrow & 0xFFFF;
    (*(u16 *)((u8 *)(p) + 0x2A)) = narrow;
    if (comparison == 6) {
        phase = 9;
        comparison = (*(u16 *)((u8 *)(p) + 0x2A));
    }
    if (comparison == 100) {
        consume(p);
    }
}
"""


def generated(source, function='f', **kwargs):
    from solver import narrow_update
    return [v for _label, family, v in narrow_update.variants(source, function, **kwargs)
            if family == 'narrow_update']


def test_random_motivator_composes_increment_store_mask_and_index_elimination():
    variants = generated(RANDOM, 'randomNextObject')
    assert any('(*(u8 *)((u8 *)(p) + 0x518))++;' in v
               and 'return *(&table + (*(u8 *)((u8 *)(p) + 0x518)));' in v
               and 'u8 idx;' not in v and 'u8 value;' not in v for v in variants)


def test_counter_motivator_handles_disjoint_reset_then_rereads_correct_width():
    variants = generated(IDLE, 'updateEndingCreditsIdleSparkle')
    assert any('(*(u16 *)((unsigned char *)p + 0x1E))++;' in v
               and '(*(u16 *)((unsigned char *)p + 0x1C))++;' in v
               and 'if ((*(u16 *)((unsigned char *)p + 0x1C)) == 5)' in v
               and 'u16 value;' not in v and 'consume(p);' in v for v in variants)


def test_signed_counter_motivator_keeps_comparison_local_and_later_reload():
    variants = generated(SIGNED)
    assert any('s16 narrow;' not in v and 'narrow &' not in v
               and '(*(u16 *)((u8 *)(p) + 0x2A)) += 1;' in v
               and v.count('comparison = (*(u16 *)((u8 *)(p) + 0x2A));') == 2
               and 'if (comparison == 6)' in v and 'consume(p);' in v for v in variants)


@pytest.mark.parametrize('source', [
    SIGNED.replace('0xFFFF', '0xFF'),
    SIGNED.replace('s16 narrow;', 's32 narrow;'),
    SIGNED.replace('s32 comparison;', 's16 comparison;'),
    SIGNED.replace('comparison = narrow &', 'comparison += narrow &'),
    SIGNED.replace('    comparison = narrow &', '    call();\n    comparison = narrow &'),
    SIGNED.replace('    comparison = narrow &', '    p = other;\n    comparison = narrow &'),
    SIGNED.replace('    comparison = narrow &', '    if (flag) comparison = narrow &'),
    SIGNED.replace('    comparison = narrow &', '    { }\n    comparison = narrow &'),
    SIGNED.replace('    comparison = narrow &', '    keep(&narrow);\n    comparison = narrow &'),
    SIGNED.replace('    if (comparison == 6)', '    sink(narrow);\n    if (comparison == 6)'),
    SIGNED.replace('    if (comparison == 6)', '    narrow = 2;\n    if (comparison == 6)'),
    SIGNED.replace('    narrow = ', '    narrow = (narrow = 2) + '),
    SIGNED.replace('0x2A)) = narrow', '0x2C)) = narrow'),
    SIGNED.replace('    (*(u16 *)((u8 *)(p) + 0x2A)) = narrow;',
                   '    if (flag) { (*(u16 *)((u8 *)(p) + 0x2A)) = narrow; }'),
    SIGNED.replace('void *p', 'volatile void *p'),
    SIGNED.replace('s16 narrow;', 's16 narrow;\n    s16 narrow;'),
])
def test_signed_counter_declines_unproved_conversion_reads_and_intervening_effects(source):
    assert not generated(source)


def test_signed_counter_constructor_handles_byte_width_and_preserves_comments():
    source = SIGNED.replace('s16 narrow;', 's8 narrow;').replace('u16', 'u8').replace('0xFFFF', '255U')
    source = source.replace('    comparison =', '    /* preserve narrow commentary */\n    comparison =', 1)
    variants = generated(source)
    assert variants and all('/* preserve narrow commentary */' in v for v in variants)
    assert any('s8 narrow;' not in v and '(*(u8 *)((u8 *)(p) + 0x2A)) += 1;' in v for v in variants)


@pytest.mark.parametrize('address', ['&comparison', '&(comparison)', '&((comparison))'])
def test_signed_counter_declines_an_escaped_comparison_local_that_can_alias_the_store(address):
    source = f'''s32 f(void *p) {{
    s8 narrow;
    s32 comparison;
    comparison = 0x12345678;
    p = (u8 *){address} + 3;
    narrow = (*(u8 *)p) + 1;
    comparison = narrow & 0xFF;
    (*(u8 *)p) = narrow;
    return comparison;
}}
'''
    assert not generated(source)


def test_needs_explicit_unsigned_read_width_and_does_not_drop_partial_mask():
    source = 'void f(void *p) {\n    (*(u16 *)p) += 1;\n    sink((*(u16 *)p) & 0xFF);\n}\n'
    variants = generated(source)
    assert variants and all('& 0xFF' in v for v in variants)
    assert not generated(source.replace('u16', 's16'))


@pytest.mark.parametrize('source', [
    'void f(volatile void *p) { (*(u8 *)p) += 1; }',
    'void f(void *p) { (*(u8 *)next()) += 1; }',
    'void f(void *p) { (*(u8 *)p++) += 1; }',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n call();\n (*(u8 *)p) = v;\n sink(v);\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n (*(u16 *)p) = 0;\n sink(v);\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n sink(&v);\n}',
    'void f(void *p) {\n u32 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n sink(v);\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n p = other;\n sink(v);\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n if (flag) { (*(u8 *)p) = v; }\n sink(v);\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n { u8 v; sink(v); }\n}',
    'void f(void *p) { for (;;) { (*(u8 *)p) += 1; } }',
    'void f(void *p) {\n#if FEATURE\n (*(u8 *)p) += 1;\n#endif\n}',
    'void f(void *p) {\n u8 v;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n keep(&(v));\n}',
    'void f(u16 *p) {\n u16 v;\n v = (*(u16 *)p) + 1;\n (*(u16 *)(p + 1)) = 0;\n (*(u16 *)p) = v;\n if (v == 2) {}\n}',
    'u8 f(u32 a, u32 b) {\n u32 p;\n u8 v;\n p = a;\n v = (*(u8 *)p) + 1;\n p = b;\n (*(u8 *)p) = v;\n return v;\n}',
])
def test_declines_visible_escape_alias_scope_and_side_effect_hazards(source):
    assert not generated(source)


def test_comments_and_other_function_are_preserved():
    source = 'void helper(void *p) { (*(u8 *)p) += 1; }\n' + RANDOM.replace('    idx =', '    /* value is a comment, not a read */\n    idx =')
    variants = generated(source, 'randomNextObject')
    assert variants and all(v.startswith('void helper(void *p) { (*(u8 *)p) += 1; }') for v in variants)
    assert all('/* value is a comment, not a read */' in v for v in variants)


def test_limits_are_deterministic_and_nonpositive_limit_declines():
    assert generated(RANDOM, 'randomNextObject', limit=0) == []
    first = generated(IDLE, 'updateEndingCreditsIdleSparkle', limit=1)
    assert len(first) == 1 and first == generated(IDLE, 'updateEndingCreditsIdleSparkle', limit=1)


def test_existing_stream_only_offers_new_family_when_explicitly_enabled():
    from solver import regalloc_mutations as rm
    source = 'void f(void *p) { (*(u8 *)p) += 1; }'
    enabled = list(islice(rm.variants(source, 'f', prefer=('narrow_update',), narrow_updates=True), 1))
    assert enabled and enabled[0][1] == 'narrow_update' and '++' in enabled[0][2]
    assert all(kind != 'narrow_update' for _label, kind, _text in rm.variants(source, 'f'))


def test_forwarded_index_is_retained_across_an_aliasing_store():
    source = RANDOM.replace('    return', '    (*(u8 *)p) = 7;\n    return')
    variants = generated(source, 'randomNextObject')
    assert variants and all('u8 idx;' in v and 'return *(&table + idx);' in v for v in variants)


def test_pointer_cast_around_addition_does_not_prove_byte_address_disjointness():
    source = 'void f(u16 *p) {\n u16 v;\n v = (*(u16 *)((u8 *)(p + 1))) + 1;\n (*(u16 *)((u8 *)(p + 3))) = 0;\n (*(u16 *)((u8 *)(p + 1))) = v;\n if (v == 2) {}\n}'
    assert not generated(source)


def test_search_can_reach_the_opt_in_construction_through_its_normal_oracle():
    from solver import regalloc_search as rs
    source = 'void f(void *p) { (*(u8 *)p) += 1; }'
    target = '0: 00000000 nop\n'
    def compile_candidate(candidate, _label):
        # A controlled oracle isolates routing; the native replay proves bytes.
        return rs.Compiled(True, '(*(u8 *)p)++;' in candidate, target)
    outcome = rs.search('f', source, compile_candidate, target, budget=16, depth=1,
                        narrow_updates=True)
    assert outcome.exact and '(*(u8 *)p)++;' in outcome.best_source


@pytest.mark.parametrize('extra, tail', [
    ('', 'return v++;'),
    ('', 'return v + mutate(p);'),
    ('x = ((*(u8 *)p) = 7);', 'return v;'),
    ('if ((p = q) == 0) return 0;', 'return v;'),
    ('', 'return s.v;'),
])
def test_declines_effects_after_last_reference_nested_writes_and_member_names(extra, tail):
    source = f'u8 f(void *p) {{\n u8 v;\n u8 x;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n {extra}\n {tail}\n}}'
    assert not generated(source)


@pytest.mark.parametrize('source', [
    'void f(u8 *p) { *(u8 *)p += 1; }',
    'u8 f(u8 *p) {\n u8 v;\n v = *(u8 *)p + 1;\n *(u8 *)p = v;\n return v;\n}',
])
def test_postfix_increment_groups_a_bare_dereference_instead_of_stepping_pointer(source):
    variants = generated(source)
    assert variants and all('(*(u8 *)p)++;' in v for v in variants)


@pytest.mark.parametrize('tail', [
    'return 0, (*mutate)(p), idx;',
    'return p = other, idx;',
])
def test_index_forward_is_retained_across_indirect_calls_and_return_assignments(tail):
    source = f'u8 f(void *p) {{\n u8 v;\n u8 idx;\n v = (*(u8 *)p) + 1;\n (*(u8 *)p) = v;\n idx = v;\n {tail}\n}}'
    variants = generated(source)
    assert variants and all('u8 idx;' in v and tail in v for v in variants)

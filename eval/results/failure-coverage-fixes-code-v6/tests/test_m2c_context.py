from pathlib import Path
from types import SimpleNamespace

import pytest

from solver import m2c_context, m2c_input


def test_indexed_extern_constraints_propagate_through_typed_temporaries():
    source = '''extern ? handles;
extern ? counts;
void f(int i) {
    OSPfs *p;
    u8 *q;
    p = (i * 0x68) + &handles;
    q = i + &counts;
}'''
    plans = m2c_context.indexed_externs(source)
    assert {p['declaration'] for p in plans} == {
        'extern OSPfs handles[];', 'extern u8 counts[];'}
    assert all(p['source_constraints'] for p in plans)


@pytest.mark.parametrize('expression', ['f(i) + &g', '(i++) + &g', '&g', 'p + q + &g'])
def test_unsupported_indexed_pointer_constraints_decline(expression):
    # The two-base case explicitly has pointer operands, not an integer index.
    source = f'extern ? g;\nvoid f(void) {{\n void *p;\n void *q;\n p = {expression};\n}}'
    assert m2c_context.indexed_externs(source) == []


def test_conflicting_pointer_types_do_not_choose_an_array_element():
    source = '''extern ? g;
void f(int i) {
    Foo *p;
    Bar *q;
    p = i + &g;
    q = i + &g;
}'''
    assert m2c_context.indexed_externs(source) == []


def test_pointer_operands_are_not_integer_indices():
    source = 'extern ? g;\nvoid f(void) {\n Foo *p;\n Foo *q;\n p = p + q + &g;\n}'
    assert m2c_context.indexed_externs(source) == []


def test_rodata_declarations_use_extracted_literals_not_source(tmp_path):
    directory = tmp_path / 'asm/data'
    directory.mkdir(parents=True)
    data = directory / 'table.s'
    data.write_text('.section .rodata\ndlabel table\n'
                    '/* A 00 */ .double 1\n.double -1.25e-3\nenddlabel table\n')
    plans = m2c_context.rodata_externs(tmp_path, 'extern ? table;\n')
    assert len(plans) == 1
    assert plans[0]['declaration'] == 'extern double table[2];'
    assert plans[0]['element_bytes'] == 8
    assert plans[0]['path'] == str(data)
    assert len(plans[0]['sha256']) == 64
    data.write_text('.section .rodata\ndlabel table\n.double 1\n.word other\nenddlabel table\n')
    assert m2c_context.rodata_externs(tmp_path, 'extern ? table;\n') == []


def test_bitcast_lowers_at_evaluation_point_not_to_numeric_conversion():
    source = 's32 f(f32 x) {\n if (x) return (bitwise s32) x;\n return 0;\n}'
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert len(plans) == 1
    assert 'union { f32 from; s32 to; } m2c_bits_0;' in fixed
    assert 'if (x) return (m2c_bits_0.from = x, m2c_bits_0.to);' in fixed
    assert '(s32) x' not in fixed
    assert m2c_context.lower_bitcasts(fixed, 'f') == (fixed, [])


def test_bitcast_name_collisions_comments_and_other_functions():
    source = ('s32 f(f32 x) {\n s32 m2c_bits_0;\n'
              '/* (bitwise s32) x */\nreturn (bitwise s32) x + (bitwise s32) x;\n}\n'
              's32 other(f32 x) { return (bitwise s32) x; }')
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert [p['local'] for p in plans] == ['m2c_bits_1', 'm2c_bits_2']
    assert '/* (bitwise s32) x */' in fixed
    assert 's32 other(f32 x) { return (bitwise s32) x; }' in fixed


@pytest.mark.parametrize('ctype', ['u8', 's8', 'u16', 's16', 'unsigned char'])
def test_narrow_bitcast_promotes_integer_word_not_float_conversion(ctype):
    source = f'f32 f({ctype} x) {{ return (bitwise f32) x; }}'
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert len(plans) == 1
    assert 'union { s32 from; f32 to; }' in fixed
    assert 'm2c_bits_0.from = x' in fixed and '(float)' not in fixed
    assert plans[0]['declared_type'] == ctype


def test_negated_integer_cast_is_reinterpreted_after_evaluation():
    source = 'f32 f(u8 x) { return (bitwise f32) -(s32) x; }'
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert len(plans) == 1 and plans[0]['operand'] == '-(s32) x'
    assert 'm2c_bits_0.from = -(s32) x' in fixed
    assert m2c_context.lower_bitcasts(fixed, 'f') == (fixed, [])


@pytest.mark.parametrize('expression', ['-(s32) x++', '--x', '-(s32) x.member', '(s32) x()'])
def test_bitcast_unary_lowering_still_declines_side_effects_and_complex_values(expression):
    source = f'f32 f(u8 x) {{ return (bitwise f32) {expression}; }}'
    assert m2c_context.lower_bitcasts(source, 'f') == (source, [])


@pytest.mark.parametrize('expression', ['x.member', 'x[0]', 'x++', 'x()', 'unknown'])
def test_complex_bitcast_operands_remain_visible(expression):
    source = f's32 f(f32 x) {{ return (bitwise s32) {expression}; }}'
    assert m2c_context.lower_bitcasts(source, 'f') == (source, [])


def test_parenthesized_member_arithmetic_bitcast_has_integer_width_guard():
    source = 'f32 f(PVoice *p) { return (bitwise f32) (p->offset - 0x40); }'
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert len(plans) == 1
    assert plans[0]['operand'] == '(p->offset - 0x40)'
    assert 'union { unsigned int from; f32 to; }' in fixed
    assert 'sizeof(((p->offset - 0x40)) | 0) == 4' in fixed
    assert 'm2c_bits_0.from = (p->offset - 0x40)' in fixed
    assert m2c_context.lower_bitcasts(fixed, 'f') == (fixed, [])


@pytest.mark.parametrize('expression', ['(x)[0]', '(x).member', '(x)++', '(s32) x', '(x; y)'])
def test_parenthesized_bitcast_does_not_consume_partial_operands(expression):
    source = f'f32 f(void) {{ return (bitwise f32) {expression}; }}'
    assert m2c_context.lower_bitcasts(source, 'f') == (source, [])


def test_bitcast_expression_runtime_bits_and_single_evaluation(tmp_path):
    import shutil
    import subprocess
    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        pytest.skip('requires host C compiler; also run in WSL')
    source = '''typedef float f32;
struct P { unsigned int offset; };
int calls;
unsigned int next(void) { ++calls; return 0x3f800040U; }
f32 f(struct P *p) { return (bitwise f32) (p->offset - 0x40); }
f32 g(void) { return (bitwise f32) (next() - 0x40); }
int main(void) {
    struct P p;
    union { float f; unsigned int u; } bits;
    p.offset = 0x3f800040U;
    if (f(&p) != 1.0f || g() != 1.0f || calls != 1) return 1;
    p.offset = 0;
    bits.f = f(&p);
    return bits.u != 0xffffffc0U;
}'''
    fixed, _ = m2c_context.lower_bitcasts(source, 'f')
    fixed, _ = m2c_context.lower_bitcasts(fixed, 'g')
    path = tmp_path / 'bits.c'
    path.write_text(fixed)
    exe = tmp_path / 'bits'
    subprocess.run([cc, '-std=c89', '-pedantic-errors', str(path), '-o', str(exe)],
                   capture_output=True, text=True, check=True)
    subprocess.run([str(exe)], check=True)


@pytest.mark.parametrize('ctype', ['float', 'double', 'unsigned long long', 'int *'])
def test_expression_bitcast_compiler_rejects_wrong_source_categories(tmp_path, ctype):
    import shutil
    import subprocess
    cc = shutil.which('cc') or shutil.which('gcc')
    if not cc:
        pytest.skip('requires host C compiler; also run in WSL')
    source = f'typedef float f32; f32 f({ctype} x) {{ return (bitwise f32) (x); }}'
    fixed, plans = m2c_context.lower_bitcasts(source, 'f')
    assert len(plans) == 1
    path = tmp_path / 'invalid.c'
    path.write_text(fixed)
    # C99 accepts long long itself, so that case tests our width guard.
    result = subprocess.run([cc, '-std=c99', '-fsyntax-only', str(path)], capture_output=True)
    assert result.returncode != 0


def test_context_subprocess_never_overwrites_shared_ctx_or_oracle(tmp_path, monkeypatch):
    (tmp_path / 'include').mkdir()
    (tmp_path / 'include/common.h').write_text('typedef int s32;')
    sentinel = tmp_path / 'ctx.c'
    sentinel.write_text('user context')
    target = tmp_path / 'target.s'
    target.write_text('glabel f\njr ra\nnop\n')
    observed = []

    def run(command, **kwargs):
        observed.append(command)
        if command[1] == '-c':
            wrapper = Path(command[-1]).read_text()
            assert wrapper == '#include "common.h"\n'
            return SimpleNamespace(returncode=0, stdout='typedef int s32;', stderr='')
        assert '--context' in command and '--no-cache' in command
        return SimpleNamespace(returncode=0, stdout='void f(void) {}', stderr='')

    monkeypatch.setattr(m2c_input.subprocess, 'run', run)
    result, metadata = m2c_input.draft(tmp_path, target, context_headers=('common.h',))
    assert result.returncode == 0 and len(observed) == 2
    assert sentinel.read_text() == 'user context'
    assert target.read_text() == 'glabel f\njr ra\nnop\n'
    assert metadata['oracle_target_unchanged']
    assert list(tmp_path.glob('.m2c-input-*')) == []


def test_context_failure_keeps_an_explicit_receipt(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('missing preprocessor')
    monkeypatch.setattr(m2c_input, 'header_draft', fail)
    rows, receipts = m2c_context.seed_variants(tmp_path, 'f', tmp_path / 'target.s', '', '')
    assert rows == []
    assert receipts[0]['status'] == 'failed'
    assert 'missing preprocessor' in receipts[0]['diagnostic']


def test_feedback_context_rejects_function_bodies_and_include_injection(tmp_path):
    target = tmp_path / 'target.s'
    target.write_text('glabel f\n')
    with pytest.raises(ValueError, match='only generated extern array'):
        m2c_input.draft(tmp_path, target, extra_declarations='void f(void) {}')
    with pytest.raises(ValueError, match='invalid context include'):
        m2c_input.draft(tmp_path, target, context_headers=('a.h"\n#include "body.c',))

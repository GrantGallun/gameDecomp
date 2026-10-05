"""Positive lifting witnesses, passive observation and source-bound guidance."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path

import pytest


def module():
    assert importlib.util.find_spec('solver.m2c_uncertainty') is not None, 'uncertainty observer is missing'
    from solver import m2c_uncertainty
    return m2c_uncertainty


def draft(tmp_path, asm, context, observe=False):
    main = pytest.importorskip('m2c.main')
    assembly = tmp_path / 'input.s'
    header = tmp_path / 'context.c'
    assembly.write_text('.text\nglabel f\n' + asm + '\njr $ra\nnop\n')
    header.write_text('typedef unsigned char u8; typedef int s32;\n' + context)
    output = io.StringIO()
    observer = module().Observer() if observe else contextlib.nullcontext()
    with contextlib.redirect_stdout(output), observer:
        rc = main.run(main.parse_flags(['--target', 'mips-ido-c', '--no-cache', '--valid-syntax',
                                      '--context', str(header), str(assembly)]))
    assert rc == 0, output.getvalue()
    source = output.getvalue()
    return source, observer.report(source) if observe else None


def test_unrecovered_record_address_fires_without_changing_c(tmp_path):
    asm = 'sll $t0, $a1, 3\naddu $t0, $a0, $t0\nlbu $v0, 6($t0)'
    ctx = 'struct S { s32 x[4]; }; u8 f(struct S *, s32);'
    original, _ = draft(tmp_path, asm, ctx)
    observed, report = draft(tmp_path, asm, ctx, True)
    assert observed == original
    assert report['status'] == 'observed' and report['hazards']
    row = next(r for r in report['hazards'] if r['kind'] == 'unrecovered-address-add')
    assert row['instruction']['mnemonic'] == 'addu'
    assert row['target_size_hypothesis'] == 16
    assert row['c_expressions'] and row['instruction']['line'] > 0
    assert report['source_sha256'] == hashlib.sha256(observed.encode()).hexdigest()


@pytest.mark.parametrize('asm,ctx', [
    ('addu $v0, $a0, $a1', 's32 f(s32, s32);'),
    ('sll $t0, $a1, 2\naddu $t0, $a0, $t0\nlw $v0, 0($t0)', 's32 f(s32 *, s32);'),
    ('addu $t0, $a0, $a1\nlbu $v0, 0($t0)', 'u8 f(u8 *, s32);'),
])
def test_recovered_array_scalar_and_byte_pointer_do_not_trigger(tmp_path, asm, ctx):
    original, _ = draft(tmp_path, asm, ctx)
    observed, report = draft(tmp_path, asm, ctx, True)
    assert original == observed and not report['hazards']


def test_observer_restores_hooks_even_on_error():
    evaluate = pytest.importorskip('m2c.evaluate')
    from m2c.translate import BinaryOp, StoreStmt
    old = (evaluate.handle_add_real, evaluate.add_imm, BinaryOp.format, StoreStmt.format)
    with pytest.raises(RuntimeError):
        with module().Observer():
            raise RuntimeError('translation failed')
    assert old == (evaluate.handle_add_real, evaluate.add_imm, BinaryOp.format, StoreStmt.format)


def test_packet_rejects_stale_c_and_preserves_unknown_mapping():
    m = module()
    source = 'void f(void) { p = q + n; }'
    report = {'status': 'observed', 'source_sha256': m.sha(source), 'hazards': [
        {'kind': 'unrecovered-address-add', 'c_expressions': ['q + n'],
         'instruction': {'mnemonic': 'addu', 'line': 3, 'synthetic': True},
         'target_size_hypothesis': None}], 'omitted_hazards': 0, 'errors': []}
    packet = m.packet(source, report)
    assert packet['regions'][0]['c_locations'][0]['start_line'] == 1
    assert packet['instruction_ownership_proven'] is False
    assert 'candidate interpretations' in m.render(source, report)
    assert 'stale' in m.render(source + '\n', report)
    assert not m.packet(source + '\n', report)['regions']


def test_capped_observation_is_not_reported_clean(tmp_path):
    pytest.importorskip('m2c.translate')
    # Force the cap with observed expressions; omitted is a visible unknown.
    m = module()
    observer = m.Observer(max_hazards=1)
    from types import SimpleNamespace
    from m2c.translate import BinaryOp, SubroutineArg
    from m2c.types import Type
    base = SubroutineArg(0, type=Type.ptr(Type.s32()))
    offset = SubroutineArg(1, type=Type.s32())
    insn = SimpleNamespace(meta=SimpleNamespace(filename=None, lineno=1, synthetic=True), mnemonic='addu')
    args = SimpleNamespace(instruction_ref=SimpleNamespace(instruction=insn))
    observer.observe_add(BinaryOp(base, '+', offset, Type.ptr()), args)
    observer.observe_add(BinaryOp(base, '+', offset, Type.ptr()), args)
    report = observer.report('')
    assert report['omitted_hazards'] == 1 and report['status'] == 'partial'


def test_late_store_is_observed_after_natural_formatting(tmp_path):
    asm = 'sll $t0, $a1, 3\naddu $t0, $a0, $t0\nsw $t0, 4($a2)'
    ctx = 'struct S { s32 x[4]; }; struct O { s32 x; struct S *q; }; void f(struct S *, s32, struct O *);'
    original, _ = draft(tmp_path, asm, ctx)
    observed, report = draft(tmp_path, asm, ctx, True)
    assert original == observed
    assert any(r['kind'] == 'late-pointer-store-view' for r in report['hazards'])


def test_recovered_byte_index_with_explicit_pointer_cast_stays_quiet(tmp_path):
    asm = 'addu $t0, $a0, $a1\nsw $t0, 4($a2)'
    ctx = 'struct S { s32 x[4]; }; struct O { s32 x; struct S *q; }; void f(u8 *, s32, struct O *);'
    original, _ = draft(tmp_path, asm, ctx)
    observed, report = draft(tmp_path, asm, ctx, True)
    assert original == observed and not report['hazards']


def test_explicit_extraction_retains_only_surviving_locations():
    m = module()
    original = '/* prelude */\nvoid f(void) { p = q + n; }\n'
    report = {'status': 'observed', 'source_sha256': m.sha(original), 'hazards': [
        {'kind': 'unrecovered-address-add', 'c_expressions': ['q + n']},
        {'kind': 'unrecovered-address-add', 'c_expressions': ['removed + expression']} ]}
    source = 'void f(void) { p = q + n; }'
    bound = m.rebind(source, report, origin_source=original)
    assert m.packet(source, bound)['unmapped_hazards'] == 1
    assert len(m.packet(source, bound)['regions']) == 1
    with pytest.raises(ValueError, match='stale'):
        m.rebind(source, report, origin_source='wrong')


def test_locations_respect_tokens_comments_and_string_literals():
    m = module()
    source = 'void f(void) { p = q + next; /* q + n */ puts("q + n"); }'
    report = {'status': 'observed', 'source_sha256': m.sha(source), 'hazards': [
        {'kind': 'unrecovered-address-add', 'c_expressions': ['q + n']}]}
    assert not m.packet(source, report)['regions']


def test_omitted_source_occurrences_remain_visible():
    m = module()
    source = 'void f(void) {\n' + 'p = q + n;\n' * 9 + '}'
    report = {'status': 'observed', 'source_sha256': m.sha(source), 'hazards': [
        {'kind': 'unrecovered-address-add', 'c_expressions': ['q + n']}]}
    packet = m.packet(source, report)
    assert len(packet['regions'][0]['c_locations']) == 8
    assert packet['regions'][0]['omitted_c_locations'] == 1
    assert packet['status'] == 'partial'


def bound_observer():
    assert importlib.util.find_spec('solver.m2c_source_binding') is not None, 'explicit m2c filename binding is missing'
    from solver.m2c_source_binding import SourceBoundObserver
    return SourceBoundObserver()


def test_shortened_m2c_filename_binds_to_explicit_source(tmp_path):
    observer = bound_observer()
    from types import SimpleNamespace
    source = tmp_path / 'input.s'
    source.write_text('/* 000008 80001000 00854021 */ addu $t0, $a0, $a1\n')
    observer.register_source(source)
    instruction = SimpleNamespace(meta=SimpleNamespace(filename='input.s', lineno=1, synthetic=False),
                                  mnemonic='addu', __str__=lambda: 'addu')
    row = observer._instruction(instruction)
    assert row['address'] == '80001000' and row['word'] == '00854021'
    assert row['assembly_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_ambiguous_shortened_filenames_have_no_byte_ownership(tmp_path):
    observer = bound_observer()
    from types import SimpleNamespace
    for directory in ('one', 'two'):
        folder = tmp_path / directory
        folder.mkdir()
        path = folder / 'input.s'
        path.write_text('/* 000008 80001000 00854021 */ addu $t0, $a0, $a1\n')
        observer.register_source(path)
    instruction = SimpleNamespace(meta=SimpleNamespace(filename='input.s', lineno=1, synthetic=False),
                                  mnemonic='addu')
    row = observer._instruction(instruction)
    assert 'word' not in row and row['byte_attribution_status'] == 'ambiguous-source-file'


@pytest.mark.parametrize('instruction_text,matches', [
    ('addu $t0, $a0, $a1', True), ('addu $t1, $a0, $a1', False)])
def test_instruction_words_must_match_actual_target_object(tmp_path, instruction_text, matches):
    import shutil
    import subprocess
    if not shutil.which('mips-linux-gnu-as'):
        pytest.skip('native MIPS assembler required')
    observer = bound_observer()
    assert hasattr(observer, 'verified_report'), 'target word verification is missing'
    from types import SimpleNamespace
    source = tmp_path/'input.s'
    source.write_text('/* 000008 80001000 00854021 */ addu $t0, $a0, $a1\n')
    observer.register_source(source)
    instruction = SimpleNamespace(meta=SimpleNamespace(filename='input.s',lineno=1,synthetic=False),mnemonic='addu')
    row = observer._instruction(instruction)
    observer._record(object(), {'kind':'unrecovered-address-add','instruction':row})
    target_source = tmp_path/'target.s'
    target_source.write_text('.text\n.set noreorder\n.globl f\n.type f,@function\nf:\n'+instruction_text+'\n.size f,.-f\n')
    target = tmp_path/'target.o'
    subprocess.run(['mips-linux-gnu-as','-EB','-march=vr4300','-o',str(target),str(target_source)],check=True)
    if matches:
        report=observer.verified_report('',target,function='f',function_address=0x80001000)
        assert report['checked_target_words']==1
        assert report['hazards'][0]['instruction']['byte_attribution_status']=='verified-target-word'
        assert row['byte_attribution_status']=='annotated-unverified'
    else:
        with pytest.raises(ValueError,match='does not match'):
            observer.verified_report('',target,function='f',function_address=0x80001000)

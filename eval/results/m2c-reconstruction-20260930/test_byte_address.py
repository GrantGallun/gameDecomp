"""Native m2c motivating and decline fixtures for the throwaway adapter."""
import contextlib
import io
from pathlib import Path
from m2c import main
from byte_address import ByteAddresses

def draft(tmp_path, asm, context, enabled):
    assembly = tmp_path/'input.s'
    header = tmp_path/'context.c'
    assembly.write_text('.text\nglabel f\n'+asm+'\njr $ra\nnop\n')
    header.write_text('typedef unsigned char u8; typedef int s32;\n'+context)
    output = io.StringIO()
    with contextlib.redirect_stdout(output), ByteAddresses(enabled=enabled) as adapter:
        rc = main.run(main.parse_flags(['--target','mips-ido-c','--no-cache','--valid-syntax',
                                      '--context',str(header),str(assembly)]))
    assert rc == 0, output.getvalue()
    return output.getvalue(), adapter.changes

def test_motivating_record_base_stride_fallback(tmp_path):
    asm = 'sll $t0, $a1, 3\naddu $t0, $a0, $t0\nlbu $v0, 6($t0)'
    ctx = 'struct S { s32 x[4]; }; u8 f(struct S *, s32);'
    original, _ = draft(tmp_path,asm,ctx,False)
    child, changes = draft(tmp_path,asm,ctx,True)
    assert changes and child != original
    assert '(u8 *)' in child
    assert any(c['kind'] == 'register-address-add' for c in changes)
    assert all(c['instruction']['line'] > 0 for c in changes)

def test_commuted_base_and_constant_step(tmp_path):
    asm = 'sll $t0, $a1, 3\naddu $t0, $t0, $a0\naddiu $t0, $t0, 8\nlbu $v0, 6($t0)'
    ctx = 'struct S { s32 x[4]; }; u8 f(struct S *, s32);'
    child, changes = draft(tmp_path,asm,ctx,True)
    assert changes and '(u8 *)' in child

def test_declines_scalar_addition(tmp_path):
    asm = 'addu $v0, $a0, $a1'
    ctx = 's32 f(s32, s32);'
    original, _ = draft(tmp_path,asm,ctx,False)
    child, changes = draft(tmp_path,asm,ctx,True)
    assert not changes and child == original

def test_preserves_recovered_word_array(tmp_path):
    asm = 'sll $t0, $a1, 2\naddu $t0, $a0, $t0\nlw $v0, 0($t0)'
    ctx = 's32 f(s32 *, s32);'
    original, _ = draft(tmp_path,asm,ctx,False)
    child, changes = draft(tmp_path,asm,ctx,True)
    assert not changes and child == original
    assert 'arg0[arg1]' in child

def test_adapter_restores_original_hooks(tmp_path):
    from m2c import evaluate
    original = (evaluate.handle_add_real, evaluate.add_imm)
    draft(tmp_path,'addu $v0, $a0, $a1','s32 f(s32,s32);',True)
    assert (evaluate.handle_add_real,evaluate.add_imm) == original

def test_byte_address_retains_required_pointer_store_view(tmp_path):
    asm = 'sll $t0, $a1, 3\naddu $t0, $a0, $t0\nsw $t0, 4($a2)'
    ctx = 'struct S { s32 x[4]; }; struct O { s32 x; struct S *q; }; void f(struct S *, s32, struct O *);'
    child, changes = draft(tmp_path,asm,ctx,True)
    assert changes
    # Address units and the store's required pointer view are distinct.
    assert 'arg2->q = (struct S *)' in child
    assert '(u8 *)' in child

def test_late_pointer_store_type_requires_explicit_view(tmp_path):
    asm = 'addu $t0, $a0, $a1\nsw $t0, 4($a2)'
    ctx = 'struct S { s32 x[4]; }; struct O { s32 x; struct S *q; }; void f(u8 *, s32, struct O *);'
    child, _ = draft(tmp_path,asm,ctx,True)
    assert 'arg2->q = (struct S *)' in child

def test_real_sprite_late_store_view(tmp_path):
    here = Path(__file__).resolve().parent
    folder = here/'portable/drafts/drawMenuSpriteClipped/byte'
    output = io.StringIO()
    with contextlib.redirect_stdout(output), ByteAddresses(enabled=True) as adapter:
        rc = main.run(main.parse_flags(['--target','mips-ido-c','--no-cache','--valid-syntax',
            '--context',str(folder/'context.c'),str(folder/'input.s')]))
    assert rc == 0
    import re
    assert re.search(r'->unk4 = \((?:struct )?T1 \*\).*var_t2 << 5',output.getvalue()), (
        [line for line in output.getvalue().splitlines() if 'var_t2 << 5' in line], adapter.store_views)
    assert adapter.store_views

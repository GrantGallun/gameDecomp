"""Address-return alternatives must fire on the observed heap-helper residual."""
from pathlib import Path
import subprocess
import pytest

ASM = '''glabel lockRelocatableHeapBlock
sll $t6, $a0, 2
addu $t6, $t6, $a0
lui $t7, %hi(gRelocatableHeapBlockPool)
addiu $t7, $t7, %lo(gRelocatableHeapBlockPool)
sll $t6, $t6, 2
addu $v0, $t6, $t7
lbu $t8, 0x11($v0)
addiu $t9, $zero, 2
beqz $t8, .Ldone
nop
sb $t9, 0x11($v0)
.Ldone:
jr $ra
nop
endlabel lockRelocatableHeapBlock
'''
CTX = {'own_prototype':'s32 lockRelocatableHeapBlock(s32 arg0);',
       'declarations':'s32 lockRelocatableHeapBlock(s32 arg0);\nextern u8 gRelocatableHeapBlockPool[8];\n',
       'evidence':{'elf_sha256':'unchanged'}}

def proposal(asm=ASM,context=None,**kwargs):
    from solver import m2c_pointer_return as pr
    return pr.propose('lockRelocatableHeapBlock',asm,context or CTX,**kwargs)

def test_motivating_indexed_global_address_survives_return():
    result=proposal()
    assert result['status']=='proposed'
    assert result['context']['own_prototype']=='void *lockRelocatableHeapBlock(s32 arg0);'
    assert result['context']['evidence']==CTX['evidence']
    assert result['witnesses'][0]['return_instruction']==11
    assert 5 in result['witnesses'][0]['address_instructions']
    assert CTX['own_prototype'].startswith('s32 ')

@pytest.mark.parametrize('tail', ['addiu $v0,$zero,7','lw $v0,0($v0)','mfc1 $v0,$f0'])
def test_return_overwrite_in_delay_slot_declines(tail):
    assert proposal(ASM.replace('jr $ra\nnop','jr $ra\n'+tail))['status']=='declined'

def test_fixed_declaration_declines():
    assert proposal(fixed_return=True)['status']=='declined'

def test_scalar_and_mixed_return_paths_decline():
    scalar='glabel lockRelocatableHeapBlock\naddu $v0,$a0,$a0\njr $ra\nnop\n'
    assert proposal(scalar)['status']=='declined'
    mixed=ASM.replace('beqz $t8, .Ldone','beqz $t8, .Lscalar').replace(
        'endlabel lockRelocatableHeapBlock','.Lscalar:\njr $ra\naddiu $v0,$zero,7\nendlabel lockRelocatableHeapBlock')
    assert proposal(mixed)['status']=='declined'

def test_unresolved_branch_and_calls_decline():
    assert proposal(ASM.replace('.Ldone\n','.Lmissing\n',1))['status']=='declined'
    assert proposal(ASM.replace('lbu $t8','jal helper\nnop\nlbu $t8'))['status']=='declined'

def test_mismatched_hi_lo_and_two_addresses_decline():
    assert proposal(ASM.replace('%lo(gRelocatableHeapBlockPool)','%lo(other)'))['status']=='declined'
    assert proposal(ASM.replace('addu $v0, $t6, $t7','addu $v0, $t7, $t7'))['status']=='declined'

def test_pointer_argument_and_multiple_exits_after_delay_slots():
    asm='''glabel lockRelocatableHeapBlock
beqz $a1,.Lsecond
nop
jr $ra
addiu $v0,$a0,1
.Lsecond:
jr $ra
addiu $v0,$a0,2
'''
    ctx={'own_prototype':'s32 lockRelocatableHeapBlock(u8 *arg0,s32 arg1);',
        'declarations':'s32 lockRelocatableHeapBlock(u8 *arg0,s32 arg1);\n','evidence':{}}
    result=proposal(asm,ctx)
    assert result['status']=='proposed' and len(result['witnesses'])==2

def test_existing_pointer_return_has_no_alternative():
    ctx={**CTX,'own_prototype':CTX['own_prototype'].replace('s32 lock','void *lock')}
    assert proposal(context=ctx)['status']=='declined'

def test_cycles_and_control_transfer_in_delay_slot_decline():
    assert proposal(ASM.replace('sb $t9, 0x11($v0)','b .Ldone\nnop').replace(
        '.Ldone:\njr $ra','.Ldone:\nb .Ldone'))['status']=='declined'
    assert proposal(ASM.replace('jr $ra\nnop','jr $ra\nb .Ldone'))['status']=='declined'

def test_second_declaration_declines():
    ctx={**CTX,'declarations':CTX['declarations']+'extern int lockRelocatableHeapBlock(int);\n'}
    assert proposal(context=ctx)['status']=='declined'

def test_targeted_delay_slot_cannot_be_skipped_on_taken_edge():
    asm='''glabel lockRelocatableHeapBlock
beqz $a0,.Lslot
nop
la $v0,base
beqz $a1,.Lret
.Lslot:
li $v0,7
la $v0,base
.Lret:
jr $ra
nop
'''
    assert proposal(asm)['status']=='declined'

def test_conditional_fallthrough_outside_boundary_declines():
    asm='''glabel lockRelocatableHeapBlock
b .Lbranch
nop
.Lret:
jr $ra
nop
.Lbranch:
la $v0,base
beqz $a0,.Lret
nop
endlabel lockRelocatableHeapBlock
'''
    assert proposal(asm)['status']=='declined'

def test_actual_draft_path_retains_scalar_parent_and_logs_pointer_hypothesis(tmp_path,monkeypatch):
    from solver import binary_type_context as bc,binary_type_draft as bd
    ws=tmp_path/'draft'; ws.mkdir(); (ws/'target.s').write_text(ASM)
    class Model:
        symbols={}
        def context(self,*args): return CTX
    monkeypatch.setattr(bc,'find_elf',lambda repo:Path('unused'))
    monkeypatch.setattr(bc,'load',lambda elf:Model())
    monkeypatch.setattr(bd,'input_hashes',lambda repo:{})
    monkeypatch.setattr(bd,'_preprocess',lambda repo,wrapper,scratch:(wrapper,{}))
    def m2c(repo,assembly,context,scratch,*,valid_syntax):
        ret='void *' if 'void *lockRelocatableHeapBlock' in context else 's32 '
        body=ret+'lockRelocatableHeapBlock(s32 arg0) { return 0; }'
        return subprocess.CompletedProcess([],0,body,'')
    monkeypatch.setattr(bd,'_m2c',m2c)
    ordinary,_=bd.variants(tmp_path,'lockRelocatableHeapBlock',ws)
    candidates,reports=bd.variants(tmp_path,'lockRelocatableHeapBlock',ws,pointer_returns=True)
    assert candidates[:len(ordinary)]==ordinary
    assert any('void *lockRelocatableHeapBlock' in c for _,c in candidates)
    assert any(r.get('pointer_return',{}).get('status')=='proposed' for r in reports)
    monkeypatch.setattr(bd,'_preprocess',lambda repo,wrapper,scratch:
        (wrapper+'\nextern int lockRelocatableHeapBlock(int);\n',{}))
    guarded,reports=bd.variants(tmp_path,'lockRelocatableHeapBlock',ws,pointer_returns=True)
    assert not any('pointer-return' in label for label,_ in guarded)
    assert any(r.get('reason')=='public context has another declaration for the function' for r in reports)

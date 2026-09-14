from solver import callback_abi,callback_repair

SOURCE='''Acmd *f(void *root,s16 *outp,s32 out,s32 offset,Acmd *p) {
    s16 sp46;
    s16 *temp_t1;
    temp_t1 = (*(s16 **)((u8 *)(root) + 0));
    return (*(void *(**)(s16 *, s16 *, s32, Acmd *, Acmd *))((u8 *)(temp_t1) + 4))(temp_t1, &sp46, out, p);
}'''
ASM='''addiu sp,sp,-80
lw t1,0(a0)
lw t9,4(t1)
move a0,t1
addiu a1,sp,70
lw t8,96(sp)
sw t8,16(sp)
jalr t9
nop
'''
MEASURE={'layouts':{'Filter':[
    {'member':'source','offset':0,'width':4,'pointee':'Filter'},
    {'member':'handler','offset':4,'width':4,'canonical':'Acmd *(*)(void *, s16 *, s32, s32, Acmd *)'}]}}


def test_callback_reconstruction_inserts_proven_fourth_argument():
    candidates=callback_abi.parameter_candidates(ASM,MEASURE)
    source,report=callback_repair.propose(SOURCE,'f',ASM,candidates)
    assert ')(temp_t1, &sp46, out, offset, p)' in source
    assert report['changes'][0]['after_arguments']==['temp_t1','&sp46','out','offset','p']


def test_callback_reconstruction_does_not_discard_side_effects():
    bad=SOURCE.replace('out, p);','out++, p);')
    assert callback_repair.propose(bad,'f',ASM,callback_abi.parameter_candidates(ASM,MEASURE))[0]==bad


def test_computed_argument_requires_existing_typed_fpr_temporary():
    asm=ASM.replace('jalr t9','mfc1 a2,f8\njalr t9')
    source=SOURCE.replace('    return','    s32 temp_f8;\n    temp_f8 = (s32) value;\n    return')
    source=source.replace('&sp46, out, p);','&sp46, (Acmd *) temp_f8, p);')
    packet=callback_abi.parameter_candidates(asm,MEASURE)
    candidate,r=callback_repair.propose(source,'f',asm,packet)
    assert r['changes'][0]['after_arguments'][2]=='temp_f8'
    assert 'temp_f8, offset, p)' in candidate
    bad=source.replace('s32 temp_f8;','void *temp_f8;')
    unchanged,r=callback_repair.propose(bad,'f',asm,packet)
    assert unchanged==bad and not r['changes'] and r['declines']

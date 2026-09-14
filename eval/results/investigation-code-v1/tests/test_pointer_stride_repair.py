from solver.pointer_stride_repair import propose

SOURCE='''void f(void) {
    Actor *p;
    p = actors;
    for (;;) { use(p); p += 16; }
}'''
ASM='''lui v1,%hi(actors)
addiu v1,v1,%lo(actors)
loop:
sw zero,0(v1)
addiu v1,v1,16
bnez a0,loop
nop
'''


def test_measured_record_step_uses_target_byte_units():
    layouts={'Actor':[{'owner_size':16}]}
    r=propose(SOURCE,'f',ASM,layouts)
    assert 'p = (Actor *)((unsigned char *)p + 16);' in r['source']
    for asm in (ASM.replace('v1,v1,16','v1,v1,4'),ASM.replace('actors','other'),ASM.replace('bnez a0,loop','nop')):
        assert not propose(SOURCE,'f',asm,layouts)['changes']
    assert not propose(SOURCE,'f',ASM,{'Actor':[{'owner_size':32}]})['changes']


def test_call_clobber_requires_saved_cursor_reload():
    asm=ASM.replace('addiu v1,v1,16','sw v1,32(sp)\njal helper\nnop\nlw v1,32(sp)\naddiu v1,v1,16')
    layouts={'Actor':[{'owner_size':16}]}
    assert propose(SOURCE,'f',asm,layouts)['changes']
    for bad in (asm.replace('lw v1,32(sp)','nop'),asm.replace('sw v1,32(sp)','sw zero,32(sp)')):
        assert not propose(SOURCE,'f',bad,layouts)['changes']

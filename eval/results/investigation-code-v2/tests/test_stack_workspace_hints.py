from solver.stack_workspace_hints import propose

SOURCE='''void f(Actor *obj) {
    ? sp40;
    ? *var_s0;
    i = 0;
    var_s0 = &sp40;
    for (;;) {
        var_s0->unk0 = obj->entries[i].value;
        var_s0->unk2 = 0;
        var_s0 += 8;
        i += 1;
        if (!(i < obj->count)) break;
    }
}'''
ASM='''glabel f
addiu sp,sp,-128
addiu s0,sp,0x40
loop:
sh v0,0(s0)
sh zero,2(s0)
addiu s0,s0,8
bnez v0,loop
nop
'''
LAYOUT={'Actor':[{'member':'count','width':2},
                 {'member':'entries','array':True,'canonical':'Entry[4]','width':32}]}


def test_loop_capacity_and_target_stride_form_explicit_hypothesis():
    r=propose(SOURCE,'f',ASM,LAYOUT)
    assert r['plans'][0]['stack_variables']==[('f',64,'s16',16)]
    assert r['plans'][0]['evidence'][0]['debt']


def test_missing_capacity_conflicting_stride_or_frame_declines():
    assert not propose(SOURCE,'f',ASM,{})['plans']
    for asm in (ASM.replace('s0,s0,8','s0,s0,4'), ASM.replace('-128','-72'),
                ASM.replace('sh zero,2(s0)','sh zero,4(s0)'),
                ASM.replace('sh v0,0(s0)','or s0,a0,zero')):
        assert not propose(SOURCE,'f',asm,LAYOUT)['plans']
    for source in (SOURCE.replace('obj->entries','other->entries'),
                   SOURCE.replace('i += 1','i += 2'),SOURCE.replace('i = 0','i = 1')):
        assert not propose(source,'f',ASM,LAYOUT)['plans']

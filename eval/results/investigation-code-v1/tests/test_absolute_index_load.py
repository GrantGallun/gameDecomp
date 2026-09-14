from solver.indexed_address_repair import absolute_load_witnesses
from solver.frontend_repair import propose

ASM='''lhu t0, 16(a1)
lui a2, %hi(table)
sll t1, t0, 3
subu t1, t1, t0
sll t1, t1, 2
addu a2, a2, t1
lh a2, %lo(table)(a2)
'''


def test_signed_width_and_parameter_index_binding(tmp_path,monkeypatch):
    assert absolute_load_witnesses(ASM,{'table':0x80001000},1,16,0x80001000,28)[0]['type']=='s16'
    from solver import m2c_adapter
    monkeypatch.setattr(m2c_adapter,'absolute_symbols',lambda _: {'table':0x80001000})
    line='    use(*(0x80001000 + ((*(u16 *)((u8 *)(p) + 0x10)) * 0x1C)));'
    source='void f(Player *a, Trigger *p) {\n'+line+'\n}\n'
    diag="candidate.c:2:9: error: indirection requires pointer operand ('unsigned int' invalid)\n    2 | "+line+'\n'
    result=propose(tmp_path,source,'f',diag,big_endian_o32=True,target_assembly=ASM)
    assert '*(s16 *)(0x80001000' in result['source']


def test_reject_wrong_identity_stride_signedness_and_clobber():
    for asm in [ASM.replace('lhu','lh'), ASM.replace('16(a1)','18(a1)'),
                ASM.replace('sll t1, t0, 3','sll t1, t0, 2'),ASM.replace('t1','t0')]:
        assert not absolute_load_witnesses(asm,{'table':0x80001000},1,16,0x80001000,28)
    assert not absolute_load_witnesses(ASM,{'table':0x80002000},1,16,0x80001000,28)

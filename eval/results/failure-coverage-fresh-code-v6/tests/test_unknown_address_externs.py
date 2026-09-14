from solver.m2c_byte_view import unknown_address_externs

SOURCE='''extern M2C_UNK packets;
void f(P *p) {
    send(1, (p->channel << 6) + &packets);
}'''
ASM='''lui t5, %hi(packets)
addiu t5, t5, %lo(packets)
sll t4, t3, 6
addu a1, t4, t5
jal send
nop
'''


def test_witnessed_shifted_global_address():
    candidate,rows=unknown_address_externs(SOURCE,'f',ASM)
    assert 'extern u8 packets[];' in candidate
    assert '(p->channel << 6) + packets' in candidate
    assert rows[0]['shift']==6


def test_unknown_address_requires_closed_uses_and_matching_target():
    for bad in [ASM.replace('t3, 6','t3, 5'),ASM.replace('jal send','jal other'),
                ASM.replace('addu a1','addu a2'),ASM.replace('%lo(packets)','%lo(other)')]:
        assert unknown_address_externs(SOURCE,'f',bad)[0]==SOURCE
    bad=SOURCE.replace('send(1,','read(packets); send(1,')
    assert unknown_address_externs(bad,'f',ASM)[0]==bad

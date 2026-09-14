from solver.hardware_environment import encoded_register_addresses
from solver.hardware_environment import register_views


def test_encoded_virtual_address_not_physical_header_macro():
    asm='''/* AB66C 800AAA6C 3C0EA460 */ lui $t6, %hi(PI_STATUS_REG)
/* AB670 800AAA70 8DCF0010 */ lw $t7, %lo(PI_STATUS_REG)($t6)
'''
    assert encoded_register_addresses(asm)['PI_STATUS_REG'][0]['address']==0xA4600010
    assert encoded_register_addresses(asm.replace('8DCF0010','ADCF0010'))=={}
    assert encoded_register_addresses('lui t6,%hi(PI_STATUS_REG)\nlw t7,%lo(PI_STATUS_REG)(t6)')=={}


def test_clobbers_labels_and_conflicting_addresses_decline():
    a='/* 0 0 3C0EA460 */ lui $t6,%hi(PORT_REG)\n'
    b='/* 4 4 8DCF0010 */ lw $t7,%lo(PORT_REG)($t6)\n'
    assert encoded_register_addresses(a+'label:\n'+b)=={}
    assert encoded_register_addresses(a+'/* 2 2 240E0000 */ addiu $t6,$zero,0\n'+b)=={}
    assert encoded_register_addresses(a+b+a+b.replace('0010','0014'))=={}


def test_register_view_uses_virtual_address_and_keeps_volatile():
    source='extern u32 PORT_REG;\nvoid f(void) {\n    value = PORT_REG;\n}\n'
    asm='/* 0 0 3C0EA460 */ lui $t6,%hi(PORT_REG)\n/* 4 4 8DCF0010 */ lw $t7,%lo(PORT_REG)($t6)\n'
    r=register_views(source,'f',asm)
    assert '(*(volatile u32 *)0xA4600010u)' in r['source']
    assert 'extern' not in r['source']
    assert register_views(source,'f',asm.replace('A460','0460'))['source']==source
    escaped=source.replace('= PORT_REG','= &PORT_REG')
    assert register_views(escaped,'f',asm)['source']==escaped


def test_branch_delay_slot_store_keeps_address_then_clears():
    asm='''/* 0 0 3C0EA460 */ lui $t6,%hi(PORT_REG)
/* 4 4 10000002 */ b done
/* 8 8 ADCF0010 */ sw $t7,%lo(PORT_REG)($t6)
/* c c ADCF0014 */ sw $t7,%lo(OTHER_REG)($t6)
'''
    r=encoded_register_addresses(asm)
    assert r['PORT_REG'][0]['address']==0xA4600010
    assert 'OTHER_REG' not in r

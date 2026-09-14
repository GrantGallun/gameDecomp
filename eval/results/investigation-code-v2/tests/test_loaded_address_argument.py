import pytest
from solver import frontend_repair

SOURCE = '''void f(struct Actor *arg0) {
    consume(arg0, (*(s32 *)((u8 *)(arg0) + 0x30)) + 6);
}
'''
ASM = '''glabel f
lw a1,0x30(a0)
addiu a1,a1,6
jal consume
nop
jr ra
nop
endlabel f
'''


def propose(monkeypatch, *, source=SOURCE, asm=ASM, o32=True, headers=True):
    monkeypatch.setattr(frontend_repair.project_headers, '_included_declarations',
        lambda *a: {'consume':['void consume(struct Actor *, u16 *);']} if headers else {})
    line=source.splitlines()[1]
    diagnostic=(f"candidate.c:2:{line.index('(*(s32')+1}: error: incompatible integer to pointer conversion passing 's32' to parameter of type 'u16 *'\n"
                f'    2 | {line}\n')
    return frontend_repair.propose('.',source,'f',diagnostic,big_endian_o32=o32,target_assembly=asm)


def test_fires_on_target_backed_byte_sum(monkeypatch):
    result=propose(monkeypatch)
    assert result['changes'][0]['kind']=='target-loaded-byte-address-argument'
    assert '(u16 *)((*(s32 *)((u8 *)(arg0) + 0x30)) + 6)' in result['source']
    assert result['loaded_address_calls'][0]['byte_step']==6


@pytest.mark.parametrize('kwargs', [
    {'asm':ASM.replace('a1,a1,6','a1,a1,12')},
    {'asm':ASM.replace('lw a1','lh a1')},
    {'o32':False}, {'headers':False},
    {'source':SOURCE.replace('\n}', '\n    arg0++;\n}')},
])
def test_declines_missing_or_conflicting_evidence(monkeypatch,kwargs):
    result=propose(monkeypatch,**kwargs)
    assert not result.get('loaded_address_calls')

import hashlib
import json
import shutil

import pytest

from solver import linked_callee
from solver import callee_execution, mips_differential as d


@pytest.mark.skipif(not all(shutil.which(name) for name in
    ('mips-linux-gnu-as','mips-linux-gnu-ld','mips-linux-gnu-objcopy')),reason='MIPS tools required')
def test_linked_binding_matches_rom_and_rejects_text_or_table_tampering(tmp_path):
    base=0x80001000
    rom=bytes.fromhex('03e0000800000000')+bytes(8)+base.to_bytes(4,'big')
    (tmp_path/'rom.bin').write_bytes(rom)
    (tmp_path/'snowboardkids.yaml').write_text(json.dumps({'sha1':hashlib.sha1(rom).hexdigest(),
        'options':{'target_path':'rom.bin'},'segments':[{'type':'code','start':0,'vram':base}]}))
    (tmp_path/'symbol_addrs.txt').write_text('callee = 0x80001000;\ntable = 0x80001010;\n')
    assembly='# MIPS_DIFF_SYMBOL table 0x80001010\n# MIPS_DIFF_DATA table 0 0\njr ra\nnop'
    result=linked_callee.bind(tmp_path,'callee',assembly,base,8)
    assert result['initialized_bytes_verified']==4 and result['size']==8
    assert result['execution_admitted'] is False
    admitted=linked_callee.admit(tmp_path,'callee',assembly,base,8)
    assert admitted.binding['execution_admitted'] and admitted.program.text_base==base
    caller=d.Program.parse('caller','addiu sp,sp,-32\nsw ra,20(sp)\njal callee\nnop\nlw ra,20(sp)\naddiu sp,sp,32\njr ra\nnop')
    run=d.execute_case(caller,d.TestCase('test',1),call_arities={'callee':0},
                       callee_environment=callee_execution.Environment({'callee':admitted}))
    assert run.status=='returned' and len(run.concrete_calls)==1, run.error
    with pytest.raises(ValueError,match='text differs'):
        linked_callee.bind(tmp_path,'callee',assembly.replace('jr ra','jr t0'),base,8)
    with pytest.raises(ValueError,match='data differs'):
        linked_callee.bind(tmp_path,'callee',assembly.replace('DATA table 0 0','DATA table 0 4'),base,8)
    with pytest.raises(ValueError,match='symbol address conflict'):
        linked_callee.bind(tmp_path,'callee',assembly.replace('SYMBOL table 0x80001010','SYMBOL table 0x80001014'),base,8)


def test_admission_policy_keeps_traps_unsupported_and_rejects_nested_calls(monkeypatch,tmp_path):
    # Stub the binary certificate to isolate runtime policy, not test ROM truth.
    monkeypatch.setattr(linked_callee,'bind',lambda *a:{'verified_trailing_nops':0})
    trapped=linked_callee.admit(tmp_path,'f','break 0\nnop',0x90000000,8)
    assert trapped.binding['deferred_traps']==[0]
    assert d.execute_case(trapped.program,d.TestCase('trap',1)).status=='unsupported'
    with pytest.raises(ValueError,match='opcode: jal'):
        linked_callee.admit(tmp_path,'f','jal g\nnop',0x90000000,8)
    with pytest.raises(ValueError,match='indirect jump'):
        linked_callee.admit(tmp_path,'f','jr t0\nnop',0x90000000,8)


def extracted_fixture(tmp_path):
    base=0x80001000
    rom=bytes.fromhex('3c08800003e0000825081010')+bytes(4)+base.to_bytes(4,'big')
    (tmp_path/'rom.bin').write_bytes(rom)
    (tmp_path/'snowboardkids.yaml').write_text(json.dumps({'sha1':hashlib.sha1(rom).hexdigest(),
        'options':{'target_path':'rom.bin'},'segments':[{'type':'code','start':0,'vram':base}]}))
    (tmp_path/'symbol_addrs.txt').write_text('callee = 0x80001000;\ntable = 0x80001010;\n')
    text='''.section .rodata
dlabel table
/* 10 80001010 80001000 */ .word .Lstart
enddlabel table
.section .text
nonmatching callee, 0xC
glabel callee
.Lstart:
/* 0 80001000 3C088000 */ lui t0,%hi(table)
/* 4 80001004 03E00008 */ jr ra
/* 8 80001008 25081010 */ addiu t0,t0,%lo(table)
endlabel callee
'''
    folder=tmp_path/'asm'
    folder.mkdir()
    (folder/'callee.s').write_text(text)
    return text


@pytest.mark.skipif(not all(shutil.which(name) for name in
    ('mips-linux-gnu-as','mips-linux-gnu-ld','mips-linux-gnu-objcopy')),reason='MIPS tools required')
def test_extracted_linked_loader_fires_without_workspace_and_checks_mnemonics(tmp_path):
    text=extracted_fixture(tmp_path)
    contract={'known':True,'source':'project-header','arity_known':True,'return_registers':[],'issues':[]}
    environment,report=callee_execution.load_binary_leaves(tmp_path,['callee'],{'callee':contract})
    assert report[0]['status']=='executable',report
    assert environment.leaves['callee'].binding['initialized_bytes_verified']==4
    assert environment.leaves['callee'].return_registers==()
    assert not (tmp_path/'nonmatchings').exists()
    with pytest.raises(ValueError,match='text differs'):
        linked_callee.from_extracted(tmp_path,'callee',text.replace('jr ra','jr t1'),return_registers=())
    rejected,report=callee_execution.load_binary_leaves(tmp_path,['callee'],{'callee':{**contract,'source':'candidate'}})
    assert not rejected.leaves and 'header ABI' in report[0]['linked_decline']


@pytest.mark.parametrize('old,new,message',[
    ('nonmatching callee, 0xC','nonmatching callee, 0x8','cover bounded'),
    ('4 80001004','8 80001004','noncontiguous'),
    ('03E00008','03E00009','annotation bytes'),
    ('.word .Lstart','.word .Lmissing','word jump tables'),
    ('10 80001010','14 80001010','annotation/label'),
    ('.section .rodata','.byte 0','data/directive'),
    ('endlabel callee','endlabel callee\nnop','tail'),
])
def test_extracted_linked_loader_declines_unbound_inputs(tmp_path,old,new,message):
    text=extracted_fixture(tmp_path)
    with pytest.raises(ValueError,match=message):
        linked_callee.from_extracted(tmp_path,'callee',text.replace(old,new),return_registers=())

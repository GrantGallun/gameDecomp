from types import SimpleNamespace
import pytest
from solver import callee_prototype_repair as repair


SOURCE='M2C_UNK sound(s16, M2C_UNK, s8, s32);\nvoid f(void) { sound(1,2,3,4); }'


def setup(monkeypatch,tmp_path,draft):
    assembly=tmp_path/'sound.s'
    assembly.write_text('glabel sound\nli v0,0\njr ra\nnop\n')
    monkeypatch.setattr(repair.project_headers,'declarations',lambda *a: [])
    monkeypatch.setattr(repair.target_intake,'resolve',lambda *a: SimpleNamespace(
        kind='disassembly',symbol='sound',path=assembly))
    monkeypatch.setattr(repair.m2c_input,'draft',lambda *a,**k: (
        SimpleNamespace(returncode=0,stdout=draft,stderr=''),{'oracle_target_unchanged':True}))
    return assembly


def test_only_interface_and_provenance_are_exported(monkeypatch,tmp_path):
    setup(monkeypatch,tmp_path,'s32 sound(s16 a, s16 b) { return a+b; }')
    r=repair.hypotheses(tmp_path,SOURCE,'f')
    p=r['prototypes'][0]
    assert p['parameters']==['s16','s16'] and p['return_type']=='s32'
    assert p['assembly_sha256'] and p['generated_source_sha256']
    assert 'return a+b' not in str(r)
    assert 'not header/ABI/effect proof' in r['authority']


@pytest.mark.parametrize('draft',[
    'M2C_UNK sound(s16 a) { return 0; }',
    's32 sound(void *a) { return 0; }',
    's32 sound() { return 0; }',
    's32 sound(s32 a,s32 b,s32 c,s32 d,s32 e) { return 0; }'])
def test_unsupported_interfaces_remain_unresolved(monkeypatch,tmp_path,draft):
    setup(monkeypatch,tmp_path,draft)
    r=repair.hypotheses(tmp_path,SOURCE,'f')
    assert not r['prototypes'] and r['declines']


def test_header_precedence_and_bounded_disassembly(monkeypatch,tmp_path):
    path=setup(monkeypatch,tmp_path,'s32 sound(void) { return 0; }')
    monkeypatch.setattr(repair.project_headers,'declarations',lambda *a: [object()])
    assert not repair.hypotheses(tmp_path,SOURCE,'f')['prototypes']
    monkeypatch.setattr(repair.project_headers,'declarations',lambda *a: [])
    path.write_text('glabel sound\n'+'nop\n'*257)
    r=repair.hypotheses(tmp_path,SOURCE,'f')
    assert not r['prototypes'] and 'bounded' in r['declines'][0]['reason']

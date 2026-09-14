from types import SimpleNamespace
from solver import local_call_interface as repair

SOURCE = '''M2C_UNK helper(void (*)(Actor *), M2C_UNK, M2C_UNK, u16);
void f(P *p) {
    helper(callback, 0, 0x64, p->index);
}'''
CALLER = '''glabel f
li a1,0
li a2,100
jal helper
nop
'''
CALLEE = '''glabel helper
addiu sp,sp,-24
sw ra,20(sp)
jal inner
nop
move v1,v0
sh a3,16(v0)
lw ra,20(sp)
jr ra
move v0,v1
'''


def setup(tmp_path, monkeypatch, callee=CALLEE):
    path = tmp_path/'helper.s'
    path.write_text(callee)
    monkeypatch.setattr(repair.project_headers, 'declarations', lambda *a: [])
    monkeypatch.setattr(repair.target_intake, 'resolve', lambda *a:
        SimpleNamespace(path=path, kind='disassembly', symbol='helper'))


def test_preserves_callback_and_all_slots(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    source, report = repair.propose(tmp_path, SOURCE, 'f', CALLER)
    assert source.startswith('void *helper(void (*)(Actor *), s32, s32, u16);')
    assert source[source.index('void f'):] == SOURCE[SOURCE.index('void f'):]
    assert report['changes'][0]['preserved_argument_slots'] == 4
    assert report['changes'][0]['callee_sha256']


def test_no_guessing_of_constants_arity_or_return(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    for source, assembly in [(SOURCE, CALLER.replace('100', '99')),
                             (SOURCE.replace('0x64', 'side_effect()'), CALLER),
                             (SOURCE.replace('0, 0x64,', '0,'), CALLER),
                             (SOURCE.replace('    helper', '    x = helper'), CALLER)]:
        assert repair.propose(tmp_path, source, 'f', assembly)[0] == source
    setup(tmp_path, monkeypatch, CALLEE.replace('move v0,v1', 'li v0,0'))
    assert repair.propose(tmp_path, SOURCE, 'f', CALLER)[0] == SOURCE


def test_header_wins_and_conflicting_return_path_declines(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(repair.project_headers, 'declarations', lambda *a: [object()])
    assert repair.propose(tmp_path, SOURCE, 'f', CALLER)[0] == SOURCE
    assert repair.returned_address_evidence(CALLEE.replace('sh a3,16(v0)', 'sh a3,16(a0)')) == []


def test_delay_slot_clobber_and_multiple_returns_fail_closed(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert repair.propose(tmp_path, SOURCE, 'f', CALLER.replace('nop', 'li a2,99'))[0] == SOURCE
    assembly = CALLEE.replace('move v1,v0', 'beqz a0,.zero\nnop\nmove v1,v0')
    assembly += '.zero:\njr ra\nli v0,0\n'
    assert repair.returned_address_evidence(assembly) == []

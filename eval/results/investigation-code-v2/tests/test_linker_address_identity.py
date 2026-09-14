import hashlib

from solver.unknowns import address_symbol_evidence
from solver.address_units import address_only_globals


def test_map_identity_is_address_only_and_receipted(tmp_path):
    (tmp_path / 'build').mkdir()
    raw = b' .bss 0x801121e0 0x2d0 path.o\n 0x801121e0 D_801121E0\n'
    (tmp_path / 'build/game.map').write_bytes(raw)
    symbols, report = address_symbol_evidence(tmp_path, ['D_801121E0', 'D_1234'])
    assert symbols == {'D_801121E0': 0x801121e0}
    assert report['unresolved'] == ['D_1234']
    assert report['inputs'][0]['sha256'] == hashlib.sha256(raw).hexdigest()
    source = '''extern M2C_UNK D_801121E0;
void f(P *p) {
    (*(s32 *)((u8 *)((&D_801121E0 + (p->index * 0xB0))) + 0x94)) = p->x;
}'''
    asm = '''lui v0,%hi(D_801121E0)
addiu v0,v0,%lo(D_801121E0)
addu t5,v0,t4
sw t3,0x94(t5)
'''
    assert 'extern u8 D_801121E0[];' in address_only_globals(source, 'f', asm, symbols)['source']


def test_conflicts_and_non_definition_lines_fail_closed(tmp_path):
    (tmp_path / 'build').mkdir()
    (tmp_path / 'symbol_addrs.txt').write_text('object = 0x1000; // size:0x4\n')
    (tmp_path / 'build/a.map').write_text(' 0x2000 object\n 0x3000 missing = .\n')
    (tmp_path / 'build/b.map').write_text(' 0x4000 object\n')
    symbols, report = address_symbol_evidence(tmp_path, ['object', 'missing'])
    assert symbols == {}
    assert report['conflicts'] == {'object': [0x1000, 0x2000, 0x4000]}
    assert report['unresolved'] == ['missing']


def test_catalog_only_and_missing_files(tmp_path):
    assert address_symbol_evidence(tmp_path, ['D_1234'])[0] == {}
    (tmp_path / 'symbol_addrs.txt').write_text('object = 0x1000; // size:0x80\n')
    assert address_symbol_evidence(tmp_path, ['object'])[0] == {'object': 0x1000}

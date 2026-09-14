from pathlib import Path
from types import SimpleNamespace
import pytest

pytest.importorskip('rabbitizer')
from miner import evidence


def setup_dump(monkeypatch,symbols,disassembly):
    def run(command,**kwargs):
        if '-d' in command:
            assert '-z' in command
        return SimpleNamespace(stdout=symbols if '-t' in command else disassembly)
    monkeypatch.setattr(evidence.subprocess,'run',run)


def test_extraction_never_extends_past_elf_function_size(monkeypatch):
    setup_dump(monkeypatch,'80000000 g F .text 00000008 f\n',
        '80000000 <f>:\n80000000: 03e00008 jr ra\n80000004: 00000000 nop\n'
        '80000008: 27bdffe0 addiu sp,sp,-32\n8000000c: 00000000 nop\n')
    funcs=evidence.disassemble(Path('x.elf'))
    assert len(funcs)==1 and funcs[0].size==8 and len(funcs[0].insns)==2


def test_alias_extent_resolves_only_at_same_address(monkeypatch):
    setup_dump(monkeypatch,'80000000 w F .text 00000000 alias\n'
        '80000000 g F .text 00000008 canonical\n80000100 g F .text 00000010 canonical\n','')
    ranges=evidence.function_ranges(Path('x.elf'))
    assert ranges[(0x80000000,'alias')]==8
    assert ranges[(0x80000100,'canonical')]==16


def test_internal_nonfunction_label_does_not_truncate_extent(monkeypatch):
    setup_dump(monkeypatch,'80000000 g F .text 0000000c f\n',
        '80000000 <f>:\n80000000: 00000000 nop\n80000004 <internal>:\n'
        '80000004: 03e00008 jr ra\n80000008: 00000000 nop\n')
    funcs=evidence.disassemble(Path('x.elf'))
    assert len(funcs)==1 and funcs[0].size==12


@pytest.mark.parametrize('size,body',[('00000000','80000000: 00000000 nop\n'),
    ('0000000c','80000000: 00000000 nop\n80000008: 00000000 nop\n'),
    ('00000008','80000000: 00000000 nop\n')])
def test_unknown_gap_or_truncated_range_fails_closed(monkeypatch,size,body):
    setup_dump(monkeypatch,f'80000000 g F .text {size} f\n','80000000 <f>:\n'+body)
    with pytest.raises(ValueError):
        evidence.disassemble(Path('x.elf'))


def test_rom_binding_checks_identity_words_and_contiguity():
    import hashlib
    rom=bytes.fromhex('03e0000800000000')
    config={'sha1':hashlib.sha1(rom).hexdigest(),
            'segments':[{'type':'code','start':0,'vram':0x80000000}]}
    func=evidence.Func(0x80000000,'f',[
        evidence.Insn(0x80000000,0x03e00008,None),evidence.Insn(0x80000004,0,None)])
    bindings=evidence.verify_rom_words([func],rom,config)
    assert bindings[0]['slice_sha256']==hashlib.sha256(rom).hexdigest()
    with pytest.raises(ValueError,match='identity'):
        evidence.verify_rom_words([func],bytes(8),config)
    func.insns[0].word=0
    with pytest.raises(ValueError,match='words differ'):
        evidence.verify_rom_words([func],rom,config)
    func.insns[0].word=0x03e00008
    func.insns[1].addr+=4
    with pytest.raises(ValueError,match='noncontiguous'):
        evidence.verify_rom_words([func],rom,config)


def test_extraction_refuses_existing_history_before_any_build_or_write(tmp_path,monkeypatch):
    path=tmp_path/'history.sqlite'
    path.write_bytes(b'preserve me')
    monkeypatch.setattr(evidence,'verify_build',lambda *a:pytest.fail('must refuse before work'))
    with pytest.raises(ValueError,match='historical evidence'):
        evidence.extract(tmp_path,'test',path)
    assert path.read_bytes()==b'preserve me'


def test_fresh_extraction_is_not_source_recovery(tmp_path,monkeypatch):
    import json
    import sqlite3
    build=tmp_path/'build'
    build.mkdir()
    (build/'game.elf').write_bytes(b'elf')
    (build/'game.map').write_text('map')
    func=evidence.Func(0x80000000,'f',[evidence.Insn(0x80000000,0,
        evidence.rabbitizer.Instruction(0,vram=0x80000000))])
    monkeypatch.setattr(evidence,'verify_build',lambda *a:'rom-identity')
    monkeypatch.setattr(evidence,'disassemble',lambda *a:[func])
    monkeypatch.setattr(evidence,'bind_repo_rom',lambda *a:[{'function':'f','bytes':4}])
    monkeypatch.setattr(evidence,'parse_tu_map',lambda *a:{'f':'build/src/a.o'})
    monkeypatch.setattr(evidence.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='objdump test'))
    path=tmp_path/'fresh.sqlite'
    evidence.extract(tmp_path,'test',path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT state,attempts,best_score FROM functions').fetchone()==('asm',0,None)
        assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0
        versions=json.loads(conn.execute('SELECT tool_versions FROM extraction').fetchone()[0])
        assert versions['source_recovery_status']=='not established by binary extraction'
        assert len(versions['elf_sha256'])==64

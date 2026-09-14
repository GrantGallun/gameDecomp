from solver import workspace
import struct
import pytest


def data_elf(*, symbol_kind=3, flags=2, relocated=False):
    names = b'\0.rodata\0.symtab\0.strtab\0.shstrtab\0.rel.rodata\0'
    payload = bytearray(52)
    rows = [(0,)*10]
    for name, kind, flag, data, link, info, size in [
        ('.rodata',1,flags,b'%d\0%6d\0',0,0,0),
        ('.symtab',2,0,bytes(16)+struct.pack('>IIIBBH',0 if symbol_kind==3 else 1,0,0,symbol_kind,0,1),3,1,16),
        ('.strtab',3,0,b'\0format\0',0,0,0),
        ('.shstrtab',3,0,names,0,0,0),
        ('.rel.rodata',9,0,struct.pack('>II',0,258) if relocated else b'',2,1,8),
    ]:
        rows.append((names.index(name.encode()),kind,flag,0,len(payload),len(data),link,info,4,size))
        payload.extend(data)
    shoff = len(payload)
    for row in rows:
        payload.extend(struct.pack('>10I',*row))
    payload[:16] = b'\x7fELF\x01\x02\x01'+bytes(9)
    struct.pack_into('>HHIIIIIHHHHHH',payload,16,1,8,1,0,0,shoff,0,52,0,0,40,len(rows),4)
    return bytes(payload)


def test_unnamed_section_symbol_supplies_actual_literal_bytes(tmp_path):
    path = tmp_path/'candidate.o'
    path.write_bytes(data_elf())
    assembly = 'lui a1,%hi(.rodata+3)\naddiu a1,a1,%lo(.rodata+3)\n'
    assert workspace._elf_data_symbols(path) == {'.rodata':b'%d\0%6d\0'}
    enriched = workspace.semantic_assembly(assembly,path)
    assert '# MIPS_DIFF_BYTES .rodata 25640025366400' in enriched


@pytest.mark.parametrize('kwargs', [{'flags':6}, {'flags':0}, {'relocated':True}])
def test_section_bytes_decline_code_unallocated_and_relocations(tmp_path,kwargs):
    path = tmp_path/'candidate.o'
    path.write_bytes(data_elf(**kwargs))
    assert workspace._elf_data_symbols(path) == {}


def test_named_objects_remain_supported(tmp_path):
    path = tmp_path/'candidate.o'
    path.write_bytes(data_elf(symbol_kind=1))
    assert workspace._elf_data_symbols(path) == {'format':b'%d\0%6d\0'}


def test_format_payload_agreement_is_not_reported_as_an_address_error():
    from solver import mips_differential as d
    def assembly(symbol,offset,payload):
        return f'''# MIPS_DIFF_BYTES {symbol} {payload}
addiu sp,sp,-64
sw ra,20(sp)
addiu a0,sp,{offset}
lui a1,%hi({symbol})
addiu a1,a1,%lo({symbol})
li a2,1
jal sprintf
nop
lw ra,20(sp)
addiu sp,sp,64
jr ra
nop'''
    target = assembly('format',24,'256400')
    candidate = assembly('.rodata',28,'256400')
    row = d.run_suite(target,candidate,(d.TestCase('literal',1),),call_arities={'sprintf':3},return_registers=())[0]
    feedback = d.causal_feedback(row)
    assert row.status == 'failed'  # output address remains unresolved, not ignored
    assert '  ! a0:' in feedback and '  = a1:' in feedback
    assert 'normalized observable agrees' in feedback
    wrong = assembly('.rodata',24,'257800')
    row = d.run_suite(target,wrong,(d.TestCase('literal',1),),call_arities={'sprintf':3},return_registers=())[0]
    assert row.status == 'failed' and '  ! a1:' in d.causal_feedback(row)


def test_semantic_assembly_attaches_only_referenced_table(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workspace, "_elf_jump_words",
        lambda _path: {
            ".rodata": [(0, 0x20), (4, 0x30)],
            "otherTable": [(0, 0x40)],
        })
    assembly = "lui at,%hi(.rodata)\nlw t0,%lo(.rodata)(at)\n"

    enriched = workspace.semantic_assembly(assembly, tmp_path / "test.o")

    assert "# MIPS_DIFF_DATA .rodata 0x0 0x20" in enriched
    assert "# MIPS_DIFF_DATA .rodata 0x4 0x30" in enriched
    assert "otherTable" not in enriched


def test_semantic_assembly_attaches_only_referenced_initialized_bytes(
        monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "_elf_jump_words", lambda _path: {})
    monkeypatch.setattr(
        workspace, "_elf_data_symbols",
        lambda _path: {"format": b"%2.2d\0", "unused": b"wrong\0"})

    enriched = workspace.semantic_assembly(
        "lui a1,%hi(format)\naddiu a1,a1,%lo(format)\n",
        tmp_path / "test.o")

    assert "# MIPS_DIFF_BYTES format 25322e326400" in enriched
    assert "unused" not in enriched


def test_semantic_assembly_attaches_referenced_linker_addresses(
        monkeypatch, tmp_path):
    repo = tmp_path / "game"
    object_path = repo / "nonmatchings" / "func" / "target.o"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"not-elf")
    (repo / "symbol_addrs.txt").write_text(
        "firstState = 0x80100000; // type:u8 size:0x1\n"
        "secondState = 0x80100004; // type:u8 size:0x1\n",
        encoding="utf-8")
    monkeypatch.setattr(workspace, "_elf_jump_words", lambda _path: {})

    enriched = workspace.semantic_assembly(
        "lui t0,%hi(secondState)\nsb zero,%lo(secondState)(t0)\n",
        object_path)

    assert "# MIPS_DIFF_SYMBOL secondState 0x80100004" in enriched
    assert "firstState" not in enriched

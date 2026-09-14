from solver import workspace


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

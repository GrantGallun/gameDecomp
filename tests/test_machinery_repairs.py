"""Four repair mechanisms that emitted uncompilable C on real residuals (2026-09-22 report card).

Each fixture is the recorded parent of a failing edge. Every test checks both halves: the mechanism no longer
performs a wrong action there, and it still performs its intended action on the shape it exists for.
"""
from pathlib import Path
import re

from solver import c89, regalloc_mutations, rewrites

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(stem):
    return (FIXTURES / f"machinery_{stem}.c").read_text(), (FIXTURES / f"machinery_{stem}.diff").read_text()


def test_single_use_inlines_at_the_counted_read_not_into_a_comment():
    source, _diff = fixture("single_use")
    rows = [(label, code) for label, _k, code in
            regalloc_mutations.single_use_local_inlines(source, "copyPackedMatrixTranslation") if ":last:" in label]
    assert rows
    for _label, code in rows:
        assert "Store the last column" in code                          # the comment is untouched
        assert not re.search(r"\blast\b", c89._mask(code))              # no dangling use of the removed local
        assert "(*(s32 *)((char *)src + 0x1C)) & 0xFFFF0000" in code or "((*(s32 *)((char *)src + 0x1C))" in code


def test_typed_index_declines_an_interior_product_and_fires_on_a_leading_one():
    source, _diff = fixture("typed_index")
    labels = [label for label, _k, _c in regalloc_mutations.typed_index_scales(source, "__osPfsRWInode")]
    assert not any("@792" in label for label in labels)
    leading = "void f(int i) {\n    g = (void *)((i * 4) + table);\n}\n"
    assert any(label.startswith("typed_index:") for label, _k, _c in regalloc_mutations.typed_index_scales(leading, "f"))


def test_reloc_symbol_keeps_its_type_preservation_contract():
    source, diff = fixture("reloc_symbol")
    labels = [r.label for r in rewrites.reloc_symbol_rewrites(source, diff)]
    assert not any("max_channels -> gSoundPriorityTable" in label for label in labels)
    # Same declared type: the substitution preserves the expression's type and still fires.
    same = source.replace("extern s32 gSoundPriorityTable[];", "extern s32 gSoundPriorityTable;")
    assert any("max_channels -> gSoundPriorityTable" in r.label for r in rewrites.reloc_symbol_rewrites(same, diff))
    # Another scalar type still fires: s8 -> u8 substitutions compiled and improved in the recorded data.
    other_scalar = source.replace("extern s32 gSoundPriorityTable[];", "extern u8 gSoundPriorityTable;")
    assert any("max_channels -> gSoundPriorityTable" in r.label
               for r in rewrites.reloc_symbol_rewrites(other_scalar, diff))
    # Not declared: the old declaration is cloned, as before.
    undeclared = source.replace("extern s32 gSoundPriorityTable[];\n", "")
    undeclared = undeclared.replace("gSoundPriorityTable[arg0]", "table[arg0]")
    assert any("max_channels -> gSoundPriorityTable" in r.label for r in rewrites.reloc_symbol_rewrites(undeclared, diff))
    # Declared across several lines: never clone a second declaration (insertHuffmanQueueNode).
    multiline = source.replace("extern s32 gSoundPriorityTable[];",
                               "extern struct Priority {\n    s32 value;\n} gSoundPriorityTable[4];")
    assert not any("max_channels -> gSoundPriorityTable" in r.label
                   for r in rewrites.reloc_symbol_rewrites(multiline, diff))


def test_pointer_table_deref_needs_a_pointer_table():
    source, diff = fixture("pointer_table_deref")
    assert not [r for r in rewrites.pointer_table_deref_rewrites(source, diff) if "gRacePlayers" in r.label]
    pointers = source.replace("extern RacePlayer gRacePlayers[8];", "extern RacePlayer *gRacePlayers[8];")
    assert [r for r in rewrites.pointer_table_deref_rewrites(pointers, diff) if "gRacePlayers" in r.label]
    # A statement that merely uses the table is not mistaken for its declaration.
    header_declared = source.replace("extern RacePlayer gRacePlayers[8];", "")
    assert [r for r in rewrites.pointer_table_deref_rewrites(header_declared, diff) if "gRacePlayers" in r.label]

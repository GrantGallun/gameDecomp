import sqlite3

from solver import m2c_adapter


def test_resolves_linker_backed_unknown_address_without_changing_logic(tmp_path):
    (tmp_path / "undefined_syms_auto.txt").write_text(
        "D_3FFFF = 0x3FFFF;\n", encoding="utf-8")
    draft = """#include "common.h"
extern ? D_3FFFF;
s32 f(s32 value) {
    if (value < 0) {
        value = (s32) (&D_3FFFF + value) >> 18;
    }
    return value;
}
"""

    adapted = m2c_adapter.resolve_absolute_unknowns(tmp_path, draft)

    assert "extern ?" not in adapted.source
    assert "(0x3FFFF + value) >> 18" in adapted.source
    assert "if (value < 0)" in adapted.source
    assert adapted.resolved_absolute_symbols == (("D_3FFFF", 0x3FFFF),)


def test_does_not_guess_type_for_nonabsolute_unknown(tmp_path):
    draft = "extern ? gUnknown;\ns32 f(void) { return (s32)&gUnknown; }\n"

    adapted = m2c_adapter.resolve_absolute_unknowns(tmp_path, draft)

    assert adapted.source == draft
    assert adapted.resolved_absolute_symbols == ()


def test_absolute_resolution_preserves_comments_strings_and_declines_mixed_uses(tmp_path):
    (tmp_path / 'undefined_syms.txt').write_text('D_1234 = 0x1234;\n')
    source = 'extern ? D_1234;\nint f(void) { return (int)&D_1234; }\n'
    decorated = source + '// &D_1234\nchar *s = "&D_1234";\n'
    adapted = m2c_adapter.resolve_absolute_unknowns(tmp_path, decorated)
    assert 'return (int)0x1234;' in adapted.source
    assert '// &D_1234' in adapted.source and '"&D_1234"' in adapted.source
    for extra in ('int x = D_1234;', 'int x = value & D_1234;',
                  'int x = value && D_1234;'):
        mixed = source + extra
        assert m2c_adapter.resolve_absolute_unknowns(tmp_path, mixed).source == mixed


def test_header_variant_combines_byte_pointer_normalization(tmp_path):
    include = tmp_path / "include" / "game"
    include.mkdir(parents=True)
    (include / "audio.h").write_text(
        "typedef struct { char pad[0x119]; signed char unk119; } State;\n"
        "int Fwobble(State *state, unsigned char *stream);\n",
        encoding="utf-8")
    draft = '''#include "common.h"
int Fwobble(State *state, u8 *stream) {
    state->unk119 = stream->unk0;
    return (int)(stream + 1);
}
'''

    variants = m2c_adapter.variants(tmp_path, "Fwobble", "", draft)

    assert "stream[0]" in variants[0].source
    header = next(row for row in variants if "project_header:" in row.label)
    assert '#include "game/audio.h"' in header.source
    assert "stream[0]" in header.source
    assert "stream->unk0" not in header.source


def test_adds_only_binary_evidenced_global_declarations(tmp_path):
    (tmp_path / "symbol_addrs.txt").write_text(
        "gCounter = 0x80123456;\n", encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE evidence (id INTEGER, kind TEXT, base TEXT, "
        "offset INTEGER, width INTEGER, signed INTEGER, is_load INTEGER)")
    conn.execute(
        "INSERT INTO evidence VALUES "
        "(7, 'mem_access', 'global:0x80123456', 0, 2, 1, 1)")
    rows = (m2c_adapter.Adaptation(
        '#include "common.h"\ns32 f(void) {\n'
        '    s32 value;\n    value = gCounter;\n    return value;\n}\n',
        "base"),)

    variants = m2c_adapter.evidence_global_variants(
        conn, tmp_path, "f", rows)

    assert len(variants) == 2
    assert "extern s16 gCounter;" in variants[1].source
    assert variants[1].declared_globals == ("gCounter",)
    assert variants[1].label == "base+binary_globals:1"

"""placeholder_declarations fires on the 2026-09-14 non-compiling census shapes and keeps known declarations."""
from solver import placeholder_declarations as pd

PROTOTYPE = """#include "common.h"
? drawMenuAsciiTextDefaultScale(?, ?, ? *, ?);      /* extern */
extern void *gRegionAllocPtr;

void func_80058360(struct RaceUiAlpha18Actor *arg0) {
    drawMenuAsciiTextDefaultScale(8, -0x48, "-Rank Prize-", 5);
}
"""

EXTERNS = """#include "common.h"
extern M2C_UNK gHuffmanNodes;
extern ? D_8013C908;
s32 func_800A8F14(OSPfs *, M2C_UNK *);              /* extern */

void insertHuffmanQueueNode(s32 arg0) {
    M2C_UNK sp26C;
    func_800A8F14(&gHuffmanNodes + arg0, &sp26C);
    D_8013C908 = arg0;
}
"""


def rows(source, function, headers=""):
    found, report = pd.propose(source, function, headers)
    return dict(found), report


def test_fires_on_unknown_prototype_and_types_every_placeholder():
    found, report = rows(PROTOTYPE, "func_80058360")
    typed = found["placeholders:typed"]
    assert "s32 drawMenuAsciiTextDefaultScale(s32, s32, void *, s32);\n" in typed
    assert "/* extern */" not in typed and "extern void *gRegionAllocPtr;" in typed
    assert report["placeholders"] == [{"name": "drawMenuAsciiTextDefaultScale", "kind": "prototype", "header_declared": False}]


def test_header_declared_placeholder_is_dropped_not_retyped():
    headers = "void drawMenuAsciiTextDefaultScale(s16 x, s16 y, char *text, s32 palette);"
    found, _ = rows(PROTOTYPE, "func_80058360", headers)
    dropped = found["placeholders:header-first"]
    assert "drawMenuAsciiTextDefaultScale(?" not in dropped and "s32 drawMenuAsciiTextDefaultScale" not in dropped
    assert '#include "common.h"\nextern void *gRegionAllocPtr;' in dropped


def test_fires_on_m2c_unk_externs_locals_and_parameter_placeholders():
    found, report = rows(EXTERNS, "insertHuffmanQueueNode")
    typed = found["placeholders:typed"]
    # Address-only use keeps m2c's byte-unit arithmetic; a value use gets a word.
    assert "extern u8 gHuffmanNodes;" in typed and "extern s32 D_8013C908;" in typed
    assert "s32 func_800A8F14(OSPfs *, void *);" in typed
    assert "    s32 sp26C;" in typed
    assert {p["kind"] for p in report["placeholders"]} == {"extern", "prototype", "local"}


def test_declines_without_placeholders_and_leaves_the_definition_alone():
    clean = "void f(s32 a) {\n    g(a ? 1 : 2);\n}\n"
    assert pd.propose(clean, "f")[0] == []
    assert not pd.signals(clean)
    assert pd.signals(PROTOTYPE) and pd.signals(EXTERNS)

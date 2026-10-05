"""solver.file_scope_objects fires on its motivating residual (guMtxIdent's stray `float sp18[4][4];`) and never
deletes a definition the source mentions elsewhere."""
from solver import file_scope_objects as fso

GU = '''#include "common.h"
#include "PR/gu.h"
float sp18[4][4];
/*
Note: assuming _MACRO_INC_GUARD is unset for .ifndef
*/
void guMtxIdent(Mtx *m) {
    float local[4][4]; guMtxIdentF((f32 (*)[4]) local);
    guMtxF2L((f32 (*)[4]) local, m);
}
'''


def test_unused_finds_the_stray_global_on_the_motivating_source():
    assert [name for name, _, _ in fso.unused(GU)] == ["sp18"]


def test_variants_delete_it_only_when_the_candidate_alone_has_data(monkeypatch, tmp_path):
    (tmp_path / "t.o").write_bytes(b"t")
    (tmp_path / "c.o").write_bytes(b"c")
    monkeypatch.setattr(fso, "_has_data", lambda obj: obj == b"c")
    out = dict(fso.variants(GU, "guMtxIdent", target_obj=tmp_path / "t.o", candidate_obj=tmp_path / "c.o"))
    assert list(out) == ["unused_file_scope_object:sp18"]
    assert "float sp18" not in out["unused_file_scope_object:sp18"] and "float local[4][4];" in out[
        "unused_file_scope_object:sp18"]
    monkeypatch.setattr(fso, "_has_data", lambda obj: True)          # target owns data too: not this residual
    assert not list(fso.variants(GU, "guMtxIdent", target_obj=tmp_path / "t.o", candidate_obj=tmp_path / "c.o"))


def test_keeps_referenced_definitions_locals_prototypes_and_externs():
    src = '''s32 gCounter;
extern s32 gOther;
typedef struct { s32 a; } Thing;
static u16 table[4] = {1, 2, 3, 4};
void helper(void);
s32 dummy;
void f(void) {
    s32 local;
    gCounter = table[0];
}
'''
    assert [name for name, _, _ in fso.unused(src)] == ["dummy"]

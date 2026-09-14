"""Tests for build-provided type detection and redeclaration stripping.

This pass DELETES declarations from candidate code, so the tests are mostly
about what it must refuse to touch. Removing a type the candidate genuinely
needs converts a clean compile error into a worse failure.
"""

from solver import buildtypes as bt


def test_strips_a_struct_the_build_defines():
    code = ("typedef struct { s32 x; s32 y; s32 z; } Vec3i;\n"
            "void f(Vec3i *v) { v->x = 0; }\n")
    out, removed = bt.strip_redeclarations(code, {"Vec3i"})
    assert removed == ["Vec3i"]
    assert "typedef struct" not in out
    assert "void f(Vec3i *v)" in out


def test_keeps_a_type_the_build_does_not_define():
    """A type the candidate invented is its own and must survive."""
    code = "typedef struct { s32 a; } MyLocalThing;\nvoid f(void) {}\n"
    out, removed = bt.strip_redeclarations(code, {"Vec3i", "Gfx"})
    assert removed == [] and out == code


def test_empty_known_set_strips_nothing():
    """If the closure could not be read, do nothing rather than guess."""
    code = "typedef struct { s32 a; } Vec3i;\n"
    out, removed = bt.strip_redeclarations(code, set())
    assert out == code and removed == []


def test_tagged_struct_definition_is_stripped_by_tag():
    code = "struct RacePlayer { s32 a; };\nvoid f(struct RacePlayer *p) {}\n"
    out, removed = bt.strip_redeclarations(code, {"RacePlayer"})
    assert removed == ["RacePlayer"]
    assert "struct RacePlayer { s32 a; };" not in out


def test_forward_declaration_is_left_alone():
    """A bare forward declaration is legal alongside the real definition."""
    code = "struct RacePlayer;\nvoid f(struct RacePlayer *p) {}\n"
    out, removed = bt.strip_redeclarations(code, {"RacePlayer"})
    assert out == code and removed == []


def test_nested_braces_do_not_truncate_the_span():
    code = ("typedef struct { struct { s32 a; } inner; s32 b; } Vec3i;\n"
            "void f(void) {}\n")
    out, removed = bt.strip_redeclarations(code, {"Vec3i"})
    assert removed == ["Vec3i"]
    assert "inner" not in out
    assert "void f(void) {}" in out


def test_scalar_typedef_of_a_build_type_is_stripped():
    code = "typedef unsigned int u32;\nvoid f(u32 x) {}\n"
    out, removed = bt.strip_redeclarations(code, {"u32"})
    assert "u32" in removed
    assert "typedef unsigned int u32;" not in out


def test_scalar_typedef_of_an_unknown_name_survives():
    code = "typedef unsigned int MyCounter;\n"
    out, removed = bt.strip_redeclarations(code, {"u32"})
    assert out == code and removed == []


def test_multiple_redeclarations_all_removed():
    code = ("typedef struct { s32 a; } Vec3i;\n"
            "typedef struct { s32 b; } Mtx;\n"
            "void f(void) {}\n")
    out, removed = bt.strip_redeclarations(code, {"Vec3i", "Mtx"})
    assert set(removed) == {"Vec3i", "Mtx"}
    assert "void f(void) {}" in out

"""Tests for the C89 repair pass.

The pass rewrites model output before it reaches the compiler, so a bug here
silently changes the meaning of candidates -- the worst possible failure for
this project, because a wrong-but-compiling candidate scores and looks like
progress. The tests below are mostly about what it must NOT do.
"""

from solver import c89


# ------------------------------------------------------------------ scalars

def test_stdint_spellings_become_build_types():
    assert "u8 x;" in c89.fix_types("uint8_t x;")
    assert "s32 y;" in c89.fix_types("int32_t y;")
    assert "u16 z;" in c89.fix_types("uint16_t z;")


def test_uintptr_becomes_word_sized():
    # MIPS N64 is ILP32, so a pointer-sized integer is 32 bits.
    assert "u32" in c89.fix_types("(uintptr_t)p")
    assert "s32" in c89.fix_types("(intptr_t)p")


def test_existing_build_types_untouched():
    src = "u8 a; s16 b; f32 c; Gfx *g;"
    assert c89.fix_types(src) == src


# ------------------------------------------------------------- attribute/inline

def test_attribute_removed_with_nested_parens():
    out = c89.strip_attributes(
        "extern void __attribute__((noreturn, aligned(8))) f(void);")
    assert "__attribute__" not in out
    assert "noreturn" not in out and "aligned" not in out
    assert " ".join(out.split()) == "extern void f(void);"


def test_inline_removed_static_kept():
    out = c89.strip_inline("static inline void f(void) {}")
    assert "inline" not in out
    assert out.startswith("static void f")


# --------------------------------------------------- declaration after statement

def test_declaration_after_statement_is_split():
    src = ("void f(RacePlayer *p) {\n"
           "    p->x = 1;\n"
           "    s32 t = p->stateTimer + 0x1E;\n"
           "    p->y = t;\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    # declaration hoisted, initialiser left where it was
    assert "s32 t;" in lines
    assert "t = p->stateTimer + 0x1E;" in lines
    assert lines.index("s32 t;") < lines.index("p->x = 1;")


def test_initialiser_does_not_move_above_its_dependency():
    """The whole point of splitting rather than hoisting."""
    src = ("void f(void) {\n"
           "    init();\n"
           "    s32 t = global_counter;\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert lines.index("init();") < lines.index("t = global_counter;")


def test_declaration_already_at_top_is_untouched():
    src = ("void f(void) {\n"
           "    s32 t = 1;\n"
           "    g(t);\n"
           "}\n")
    assert c89.hoist_declarations(src) == src


def test_for_init_declaration_is_hoisted():
    src = ("void f(void) {\n"
           "    g();\n"
           "    for (s32 i = 0; i < 8; i++) {\n"
           "        h(i);\n"
           "    }\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert "s32 i;" in lines
    assert any(l.startswith("for (i = 0;") for l in lines)


# ------------------------------------------------------------------- safety

def test_labels_and_cases_are_not_statements():
    """A label before a declaration must not force a hoist."""
    src = ("void f(void) {\n"
           "top:\n"
           "    s32 t = 1;\n"
           "}\n")
    assert c89.hoist_declarations(src) == src


def test_function_parameters_are_not_declarations():
    src = ("void f(void) {\n"
           "    g();\n"
           "    h(1);\n"
           "}\n")
    assert c89.hoist_declarations(src) == src


def test_comments_and_strings_are_not_parsed_as_code():
    src = ('void f(void) {\n'
           '    g();\n'
           '    /* s32 fake = 1; */\n'
           '    puts("s32 also = 2;");\n'
           '}\n')
    out = c89.hoist_declarations(src)
    assert "fake" not in out.replace("/* s32 fake = 1; */", "")
    assert '"s32 also = 2;"' in out


def test_idempotent():
    src = ("void f(RacePlayer *p) {\n"
           "    p->x = 1;\n"
           "    s32 t = p->y;\n"
           "}\n")
    once = c89.to_c89(src)
    assert c89.to_c89(once) == once


def test_file_scope_declarations_untouched():
    src = ("extern u8 gRacePlayers[];\n"
           "static s32 gCount;\n"
           "void f(void) {}\n")
    assert c89.hoist_declarations(src) == src


def test_struct_member_declarations_untouched():
    src = ("typedef struct {\n"
           "    s32 a;\n"
           "    s16 b;\n"
           "} Foo;\n")
    assert c89.hoist_declarations(src) == src


# ------------------------------------- regressions found by eval/replay.py

def test_const_declaration_is_never_split():
    """Splitting a const makes the assignment a write to a constant.

    Found by replay on getRaceCourseSurfaceSpawnTransform: the candidate
    compiled at 35.78 before this pass and not at all after it. IDO: "Change
    value for constant variable."
    """
    src = ("void f(Surface *s) {\n"
           "    g();\n"
           "    const s16 ref = s->idx;\n"
           "}\n")
    assert c89.hoist_declarations(src) == src


def test_struct_members_are_never_hoisted_past_pointer_or_array_members():
    src = """struct CallbackTask {
    struct CallbackTask *prev;
    struct CallbackTask *next;
    void (*callback)(void *);
    u16 type;
    u16 priority;
    u8 pad[6];
    u16 isActive;
};
"""
    assert c89.to_c89(src) == src


def test_local_aggregate_and_nested_union_preserve_member_order():
    src = """void f(void) {
    struct State
    {
        u8 pad[6];
        union {
            u8 bytes[4];
            u32 word;
        } value;
        u16 tail;
    } state;
    use(&state);
}
"""
    assert c89.hoist_declarations(src) == src


def test_static_local_is_never_split():
    """A static initialises once at load; an assignment runs every call."""
    src = ("void f(void) {\n"
           "    g();\n"
           "    static s32 counter = 0;\n"
           "}\n")
    assert c89.hoist_declarations(src) == src


def test_pointer_declaration_is_recognised_as_a_declaration():
    """The bug behind the regression: a pointer decl read as a statement.

    `const struct S *p = ...;` failed to parse, so it counted as a statement
    and the NEXT declaration was wrongly hoisted past it.
    """
    src = ("void f(s32 a) {\n"
           "    s32 idx = a * 8;\n"
           "    const struct RaceCourseSurface *surface = &gSurfaces[a];\n"
           "    s16 ref = surface->referenceCoordIndex;\n"
           "}\n")
    # every line is a declaration, so nothing may move
    assert c89.hoist_declarations(src) == src


def test_pointer_type_survives_the_split():
    src = ("void f(void) {\n"
           "    g();\n"
           "    Gfx *p = gRegionAllocPtr;\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert "Gfx *p;" in lines
    assert "p = gRegionAllocPtr;" in lines


def test_trailing_comment_does_not_defeat_the_split():
    """Matching ran on the masked line but the edit re-matched the original.

    A trailing comment made the second match fail, so the pass silently did
    nothing -- 5 of the 10 residual syntax errors after the first version.
    """
    src = ("void f(s32 diff) {\n"
           "    g();\n"
           "    s32 quotient = diff / 0x6400;   /* truncates toward 0 */\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert "s32 quotient;" in lines
    assert any(l.startswith("quotient = diff / 0x6400;") for l in lines)
    # the comment must survive -- it is the model's own reasoning
    assert "/* truncates toward 0 */" in out


def test_trailing_comment_on_a_bare_declaration():
    src = ("void f(void) {\n"
           "    g();\n"
           "    s32 t;   /* scratch */\n"
           "}\n")
    out = c89.hoist_declarations(src)
    assert "/* scratch */" in out
    assert out.count("s32 t;") == 1


def test_for_init_declaration_hoisted_even_as_first_statement():
    """C89's for-grammar takes an expression, never a declaration.

    So this is illegal even at the top of a block, where an ordinary
    declaration would be fine. The pass previously only fired after a
    statement and left these alone.
    """
    src = ("void f(u32 w) {\n"
           "    for (u32 j = 0; j < w; j++) {\n"
           "        h(j);\n"
           "    }\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert "u32 j;" in lines
    assert any(l.startswith("for (j = 0;") for l in lines)


def test_for_init_pointer_declaration():
    src = ("void f(s8 *buf) {\n"
           "    for (s8 *p = buf; *p != 0; p++) {\n"
           "        h(p);\n"
           "    }\n"
           "}\n")
    out = c89.hoist_declarations(src)
    lines = [l.strip() for l in out.splitlines()]
    assert "s8 *p;" in lines
    assert any(l.startswith("for (p = buf;") for l in lines)


# -------------------------------------------------------- target linkage
#
# `strip_inline` deliberately keeps `static` (test_inline_removed_static_kept above), and that is
# right for a text rewrite with no knowledge of which identifier is the target. `public_definition`
# is the separate, name-aware pass that has to drop it, because IDO does not emit an unreferenced
# `static` function and the object comes back with no `.text` -- "Compiled object has no text
# symbols", which reads as a type/include problem and is not one.
#
# The motivating residual is real model output from the admission bucket (receipts 31124/31125,
# 2026-09-16): correct C that IDO rejected at the opening brace because `inline` is C99, and that
# then produced no text symbols because `static` was left behind.

def test_public_definition_fires_on_the_motivating_residual():
    src = ("#include \"common.h\"\n"
           "\n"
           "static inline void *acquireRelocatableHeapBlockMetadata(void)\n"
           "{\n"
           "    return 0;\n"
           "}\n")
    out = c89.public_definition(c89.to_c89(src), "acquireRelocatableHeapBlockMetadata")
    assert "inline" not in out
    assert "static" not in out
    assert out.count("acquireRelocatableHeapBlockMetadata") == 1


def test_public_definition_pointer_return_is_not_a_silent_decline():
    # The first version of this used a return-type regex and silently declined on a pointer return,
    # which is the silent-decline shape CLAUDE.md catalogues. This is the regression guard.
    src = "static void *f(void) {\n    return 0;\n}\n"
    out = c89.public_definition(src, "f")
    assert out.startswith("void *f(void)")


def test_public_definition_keeps_helper_static():
    src = ("static s32 helper(s32 a) {\n"
           "    return a;\n"
           "}\n"
           "\n"
           "static s32 target(s32 a) {\n"
           "    return helper(a);\n"
           "}\n")
    out = c89.public_definition(src, "target")
    assert "static s32 helper" in out            # a helper's linkage is not ours to change
    assert "static s32 target" not in out
    assert "s32 target(s32 a)" in out


def test_public_definition_declines_when_there_is_no_static():
    src = "void f(void) {\n    return;\n}\n"
    assert c89.public_definition(src, "f") == src


def test_public_definition_leaves_file_scope_data_static():
    src = ("static s32 gTable[4];\n"
           "\n"
           "s32 target(void) {\n"
           "    return gTable[0];\n"
           "}\n")
    out = c89.public_definition(src, "target")
    assert "static s32 gTable[4];" in out

"""Tests for diff-directed layout repair.

This pass rewrites a candidate's struct from the oracle's diff, so the tests
are mostly about refusing to act on an unreliable reading. A wrong repair here
does not just fail -- it can move every subsequent field.
"""

from solver import diffrepair


def test_arithmetic_padding_is_counted_and_layout_repair_fires():
    code = ('struct S {\n    char pad[0x10 - 0x04];\n'
            '    int value;\n};\n')
    region = diffrepair._struct_regions(code)[0]
    fields = diffrepair.region_fields(code, region)
    assert [(m.group('name'), off, size) for m, off, size in fields] == [
        ('pad', 0, 12), ('value', 12, 4)]
    repaired, changed = diffrepair.apply_constraints_in(code, region, {12:16})
    assert changed
    fields = diffrepair.region_fields(repaired, diffrepair._struct_regions(repaired)[0])
    assert next(off for m, off, _ in fields if m.group('name') == 'value') == 16


def test_unparsed_members_and_invalid_extents_never_shift_later_fields():
    for member in ('char pad[COUNT];', 'char pad[010];', 'char pad[0x10 - 0x20];',
                   'char pad[2147483647 + 1 - 1];', 'char pad[0];',
                   'void (*callback)(void);', 'int bits:3;', 'int a, b;'):
        code = 'typedef struct {\n    '+member+'\n    int value;\n} S;\n'
        region = diffrepair._struct_regions(code)[0]
        assert diffrepair.region_fields(code, region) == [], member
        assert diffrepair._fields(code) == [], member
        assert 'S' not in diffrepair.type_sizes(code), member
        assert diffrepair.reorder_fields(code, {0:8,4:12}) == (code,False)


def test_generated_filter_arithmetic_padding_offsets():
    code = '''struct Filter {
    void *unk0;
    char pad1[0x38 - 0x04];
    int unk38;
    char pad2[0x48 - 0x3C];
    int unk48;
    void **unk40;
    short unk1A;
    char pad4[0x40 - 0x1C];
    void *unk3C;
};
'''
    fields = diffrepair.region_fields(code, diffrepair._struct_regions(code)[0])
    offsets = {m.group('name'):off for m, off, _ in fields}
    assert {name:offsets[name] for name in ('unk38','unk48','unk40','unk1A','unk3C')} == {
        'unk38':0x38, 'unk48':0x48, 'unk40':0x4C, 'unk1A':0x50, 'unk3C':0x78}


def _d(pairs):
    out = []
    for a, b in pairs:
        out.append("-" + a)
        out.append("+" + b)
    return "\n".join(out)


def test_reads_the_expected_offset_out_of_the_diff():
    m, dropped = diffrepair.constraints(
        _d([("lbu v1,0x24(a0)", "lbu v1,0(a0)")]))
    assert m == {0: 0x24} and dropped == {}


def test_ignores_a_width_difference():
    """sw vs sb is the field's TYPE, not its position."""
    m, _ = diffrepair.constraints(_d([("sw t1,0x1c(t2)", "sb t1,0x1c(t2)")]))
    assert m == {}


def test_ignores_a_different_base_register():
    """A different base is a different object, not a moved field."""
    m, _ = diffrepair.constraints(_d([("lw v0,0x10(a0)", "lw v0,0x20(a1)")]))
    assert m == {}


def test_contradictory_constraints_are_dropped_not_guessed():
    """One produced offset claiming two expected offsets means the pairing
    is unreliable for that base, so the whole base is discarded."""
    m, dropped = diffrepair.constraints(_d([
        ("lw v0,0x10(a0)", "lw v0,0(a0)"),
        ("lw v1,0x20(a0)", "lw v1,0(a0)"),
    ]))
    assert m == {} and dropped == {"a0": "contradictory"}


def test_different_base_registers_are_kept_apart():
    """a0 and v0 are generally different objects.

    Merging them produced a non-monotonic mapping on
    updateRaceSplitscreenSelectPlayerCountIcons -- 0 -> 0x24 from one base and
    4 -> 0x18 from another -- which describes no single struct.
    """
    m, _dropped = diffrepair.constraints(_d([
        ("lbu v1,0x24(a0)", "lbu v1,0(a0)"),
        ("lh v1,0x18(v0)", "lh v1,4(v0)"),
    ]))
    # both are internally consistent, so both survive, but they were grouped
    assert m == {0: 0x24, 4: 0x18}


def test_non_monotonic_constraints_are_kept_as_reordering_signal():
    """Once pairs come from a real alignment, non-monotonic means REORDER.

    It used to be dropped as probable line drift, which was correct under
    positional zip() pairing and discards real signal under alignment.
    """
    m, dropped = diffrepair.constraints(_udiff([
        "-lw v0,0x20(a0)",
        "+lw v0,0(a0)",
        "-lw v1,0x10(a0)",
        "+lw v1,4(a0)",
    ]))
    assert m == {0: 0x20, 4: 0x10}
    assert dropped == {}
    assert diffrepair.order_violation(m)


def test_fields_are_scoped_to_struct_bodies():
    """File-level typedefs and function locals are not struct fields.

    Walking every declaration with one running offset counted
    `typedef unsigned char u8;` as a field at offset 0, so every computed
    offset was meaningless.
    """
    body = ("typedef unsigned char u8;\n"
            "struct T {\n"
            "    u8 a;\n"
            "    u8 b;\n"
            "};\n"
            "void f(void) {\n"
            "    s32 local;\n"
            "}\n")
    got = [(m.group("name"), off) for m, off, _s in diffrepair._fields(body)]
    assert got == [("a", 0), ("b", 1)]


def test_identical_offsets_produce_no_constraint():
    m, _ = diffrepair.constraints(_d([("lw v0,0x10(a0)", "lw v0,0x10(a0)")]))
    assert m == {}


# ------------------------------------------------------------- application

def test_inserts_padding_to_reach_the_expected_offset():
    body = ("typedef struct {\n"
            "    u8 a;\n"
            "    u8 b;\n"
            "} T;\n")
    out, changed = diffrepair.apply_constraints(body, {0: 0x24})
    assert changed and "dpad00[0x24]" in out


def test_later_fields_shift_with_an_early_pad():
    """A struct missing an early pad has EVERY later field wrong."""
    body = ("typedef struct {\n"
            "    u8 a;\n"
            "    u8 b;\n"
            "} T;\n")
    # a: 0 -> 0x24 means b moves 0x24 too; a constraint on b at its ORIGINAL
    # offset 1 must not double-count
    out, changed = diffrepair.apply_constraints(body, {0: 0x24, 1: 0x25})
    assert changed
    assert out.count("dpad") == 1


def test_no_constraints_means_no_change():
    body = "typedef struct {\n    u8 a;\n} T;\n"
    out, changed = diffrepair.apply_constraints(body, {})
    assert not changed and out == body


def test_never_moves_a_field_backwards():
    body = ("typedef struct {\n"
            "    char pad[0x40];\n"
            "    u8 a;\n"
            "} T;\n")
    # a already sits at 0x40; a constraint saying 0x10 must be ignored
    out, changed = diffrepair.apply_constraints(body, {0x40: 0x10})
    assert not changed


def test_repair_reports_what_it_used():
    body = "typedef struct {\n    u8 a;\n} T;\n"
    _out, changed, info = diffrepair.repair(
        body, _d([("lbu v1,0x24(a0)", "lbu v1,0(a0)")]))
    assert changed and info["constraints"] == 1 and info["dropped"] == 0


def test_reordering_constraints_are_detected_not_applied():
    """Padding preserves order, so a swap is a different repair entirely.

    Measured on updateRaceSplitscreenSelectPlayerCountIcons: the field at 1
    must reach 0x26 while the field at 2 must reach 0x25.
    """
    assert diffrepair.order_violation({1: 0x26, 2: 0x25})
    assert not diffrepair.order_violation({0: 0x24, 1: 0x25, 2: 0x26})

    body = ("struct T {\n    u8 a;\n    u8 b;\n    u8 c;\n};\n")
    _out, changed, info = diffrepair.repair(
        body, "-lbu v1,0x26(a0)\n+lbu v1,1(a0)\n-lbu v1,0x25(a0)\n+lbu v1,2(a0)")
    assert not changed and info["needs_reorder"]


# --------------------------------------------------- field reordering

def test_reorders_fields_onto_their_stated_offsets():
    """The exact shape from updateRaceSplitscreenSelectPlayerCountIcons."""
    body = ("struct T {\n"
            "  u8 state;\n"
            "  u8 playerCount;\n"
            "  u8 spawnTimer;\n"
            "  s16 iconX[5];\n"
            "};\n")
    out, changed = diffrepair.reorder_fields(
        body, {0: 0x24, 1: 0x26, 2: 0x25, 4: 0x18})
    assert changed
    # iconX first at 0x18, then state, spawnTimer, playerCount
    order = [l.strip() for l in out.splitlines()
             if ";" in l and not l.strip().startswith("}")]
    names = [l for l in order if "rpad" not in l]
    assert names == ["s16 iconX[5];", "u8 state;", "u8 spawnTimer;",
                     "u8 playerCount;"]
    assert "rpad00[0x18]" in out


def test_reordering_declines_when_a_field_is_unconstrained():
    """An unplaced field could belong anywhere; guessing invents layout."""
    body = ("struct T {\n"
            "  u8 a;\n"
            "  u8 b;\n"
            "};\n")
    out, changed = diffrepair.reorder_fields(body, {0: 0x24})
    assert not changed and out == body


def test_reordering_declines_on_overlapping_placements():
    """Two fields cannot occupy the same bytes."""
    body = ("struct T {\n"
            "  s32 a;\n"
            "  s32 b;\n"
            "};\n")
    out, changed = diffrepair.reorder_fields(body, {0: 0x10, 4: 0x12})
    assert not changed and out == body


def test_reordering_preserves_names_and_types():
    """Only layout changes -- every p->field in the body still resolves."""
    body = ("struct T {\n"
            "  u8 alpha;\n"
            "  s16 beta;\n"
            "};\n")
    out, _changed = diffrepair.reorder_fields(body, {0: 0x8, 2: 0x4})
    assert "u8 alpha;" in out and "s16 beta;" in out


def test_function_body_is_not_a_struct_region():
    """`void f(struct X *row) {` must not be parsed as a struct definition.

    It was, because the pattern allowed a ')' between `struct` and '{', so the
    function's LOCALS were counted as fields at offsets 0, 4, 8 on
    updateRaceSplitscreenSelectPlayerCountIcons.
    """
    body = ("struct T {\n"
            "  u8 a;\n"
            "};\n"
            "void f(struct T *row)\n"
            "{\n"
            "  s32 local;\n"
            "  s32 other;\n"
            "}\n")
    got = [m.group("name") for m, _o, _s in diffrepair._fields(body)]
    assert got == ["a"]


# ----------------------------------------------- stream alignment

def _udiff(lines):
    """A unified diff body with context, as the oracle emits."""
    return "--- target\n+++ candidate\n@@ -1,5 +1,5 @@\n" + "\n".join(lines)


def test_streams_are_reconstructed_from_context():
    t, c = diffrepair._streams(_udiff([
        " addiu sp,sp,-0x18",
        "-lbu v1,0x24(a0)",
        "+lbu v1,0(a0)",
        " move a3,a0",
    ]))
    assert t == ["addiu sp,sp,-0x18", "lbu v1,0x24(a0)", "move a3,a0"]
    assert c == ["addiu sp,sp,-0x18", "lbu v1,0(a0)", "move a3,a0"]


def test_offset_is_blanked_for_alignment():
    assert diffrepair._blank_offset("lbu v1,0x24(a0)") == "lbu v1,OFF(a0)"
    assert diffrepair._blank_offset("move a3,a0") == "move a3,a0"


def test_alignment_pairs_only_offset_differences():
    pairs = diffrepair.aligned_pairs(_udiff([
        " move a3,a0",
        "-lbu v1,0x24(a0)",
        "+lbu v1,0(a0)",
    ]))
    assert pairs == [("lbu v1,0x24(a0)", "lbu v1,0(a0)")]


def test_alignment_survives_an_unpaired_instruction():
    """The whole reason for this: positional zip drifts after an odd line.

    The target has an extra `nop`. Under zip(minus, plus) every later pair is
    shifted by one and the constraints become nonsense; alignment pairs them
    correctly.
    """
    pairs = diffrepair.aligned_pairs(_udiff([
        "-nop",
        "-lbu v1,0x24(a0)",
        "+lbu v1,0(a0)",
        "-lh t8,0x18(v0)",
        "+lh t8,4(v0)",
    ]))
    assert ("lbu v1,0x24(a0)", "lbu v1,0(a0)") in pairs
    assert ("lh t8,0x18(v0)", "lh t8,4(v0)") in pairs


def test_structural_difference_is_not_paired():
    """A different opcode is not a moved field."""
    pairs = diffrepair.aligned_pairs(_udiff([
        "-b 390",
        "+beqz v1,390",
    ]))
    assert pairs == []


# ------------------------------------------------- field width (retyping)

def test_width_constraint_read_from_opcode():
    """`sw` writes four bytes; `sb` one. The pair states the field's TYPE."""
    w = diffrepair.width_constraints(_udiff([
        "-sw t1,0x1c(t2)",
        "+sb t1,0x1c(t2)",
    ]))
    assert w == {0x1c: (4, None)}      # a store says nothing about signedness


def test_unsigned_load_carries_signedness():
    w = diffrepair.width_constraints(_udiff([
        "-lbu v0,0x10(a0)",
        "+lw v0,0x10(a0)",
    ]))
    assert w == {0x10: (1, True)}


def test_signed_load_carries_signedness():
    w = diffrepair.width_constraints(_udiff([
        "-lh v0,0x10(a0)",
        "+lw v0,0x10(a0)",
    ]))
    assert w == {0x10: (2, False)}


def test_offset_difference_is_not_a_width_constraint():
    w = diffrepair.width_constraints(_udiff([
        "-lw v0,0x24(a0)",
        "+lw v0,0(a0)",
    ]))
    assert w == {}


def test_conflicting_widths_are_dropped():
    w = diffrepair.width_constraints(_udiff([
        "-sw t1,0x10(a0)",
        "+sb t1,0x10(a0)",
        "-sh t2,0x10(a0)",
        "+sb t2,0x10(a0)",
    ]))
    assert w == {}


def test_apply_widths_retypes_a_scalar():
    body = "struct T {\n    u8 a;\n    u8 b;\n};\n"
    out, changed = diffrepair.apply_widths(body, {1: (4, False)})
    assert changed and "s32 b;" in out and "u8 a;" in out


def test_apply_widths_leaves_arrays_alone():
    """An array accessed at a different width is ambiguous -- element type or
    index arithmetic -- and retyping it would guess between them."""
    body = "struct T {\n    s16 icons[5];\n};\n"
    out, changed = diffrepair.apply_widths(body, {0: (4, False)})
    assert not changed and out == body


def test_apply_widths_no_op_when_already_right():
    body = "struct T {\n    s32 a;\n};\n"
    out, changed = diffrepair.apply_widths(body, {0: (4, False)})
    assert not changed


def test_declaration_behind_an_inline_comment_is_counted():
    """`/* 0x00 .. 0x17 */ char pad0[0x18];` is a field, not a comment.

    Anchoring the declaration pattern at the line start made every
    comment-prefixed padding declaration invisible, so the pads were not
    counted and every later field got the wrong running offset. On
    updateRaceSetupFourPlayerOption that placed `state` at 0x02 instead of
    0x1C and the diff's constraint matched nothing.
    """
    body = ("struct T {\n"
            "/* 0x00 .. 0x17 */ char pad0[0x18];\n"
            "    s16 x;                     /* offset 0x18 */\n"
            "    /* 0x1A */               char pad1[0x1];\n"
            "    u8 state;                  /* offset 0x1C */\n"
            "};\n")
    got = [(m.group("name"), off) for m, off, _s in diffrepair._fields(body)]
    assert got == [("pad0", 0x00), ("x", 0x18), ("pad1", 0x1A),
                   ("state", 0x1B)]


# --------------------------------------------- per-object constraint sets

def _mdiff(pairs):
    out = ["--- target", "+++ candidate", "@@ -1,9 +1,9 @@"]
    for a, b in pairs:
        out.append("-" + a)
        out.append("+" + b)
    return "\n".join(out)


def test_one_ambiguous_offset_does_not_discard_its_whole_base():
    """v0 carried twelve unanimous constraints and one ambiguous offset, and
    the old grouping threw away all thirteen."""
    diff = _mdiff([
        ("lw t0,0x4a0(v0)", "lw t0,0x24(v0)"),
        ("lw t1,0x4a4(v0)", "lw t1,0x28(v0)"),
        ("lw t2,0x4a8(v0)", "lw t2,0x2c(v0)"),
        ("lw t3,0x28(v0)", "lw t3,4(v0)"),
        ("lw t4,0x2c(v0)", "lw t4,4(v0)"),   # same produced offset, two wants
    ])
    sets = diffrepair.constraint_sets(diff)
    assert len(sets) == 1
    base, mapping = sets[0]
    assert base == "v0"
    assert mapping == {0x24: 0x4a0, 0x28: 0x4a4, 0x2c: 0x4a8}
    assert 4 not in mapping, "the ambiguous offset must drop only itself"


def test_two_bases_are_never_merged_into_one_conflict():
    """a2 and t6 are different objects that share the number 0."""
    diff = _mdiff([
        ("lw v0,0x20(t6)", "lw v0,0(t6)"),
        ("lbu t9,0x1e(a2)", "lbu t9,4(a2)"),
        ("lh t2,0x1c(a2)", "lh t2,2(a2)"),
        ("lbu t8,0x18(a2)", "lbu t8,0(a2)"),
    ])
    sets = dict(diffrepair.constraint_sets(diff))
    assert sets["t6"] == {0: 0x20}
    assert sets["a2"] == {0: 0x18, 2: 0x1c, 4: 0x1e}


def test_a_set_needing_reorder_is_left_out():
    diff = _mdiff([
        ("lh t0,0x26(a0)", "lh t0,1(a0)"),
        ("lh t1,0x25(a0)", "lh t1,2(a0)"),
    ])
    assert diffrepair.constraint_sets(diff) == []


def test_region_fields_scopes_offsets_to_one_struct():
    code = ("typedef struct {\n    u8 a;\n    u8 b;\n} A;\n"
            "typedef struct {\n    u32 x;\n    u16 y;\n} B;\n")
    regions = diffrepair._struct_regions(code)
    assert len(regions) == 2
    offs = [[o for _m, o, _s in diffrepair.region_fields(code, r)]
            for r in regions]
    assert offs == [[0, 1], [0, 4]]


def test_apply_constraints_in_pads_only_the_named_struct():
    code = ("typedef struct {\n    u8 a;\n    u8 b;\n} A;\n"
            "typedef struct {\n    u8 x;\n    u8 y;\n} B;\n")
    regions = diffrepair._struct_regions(code)
    out, changed = diffrepair.apply_constraints_in(code, regions[1], {0: 4})
    assert changed
    assert out.count("dpad") == 1
    head, _tail = out.split("} A;")
    assert "dpad" not in head, "the first struct must be untouched"


# ------------------------------------------------ nested user-defined types

NESTED = (
    "typedef struct {\n"
    "    s32 x;\n"
    "    s32 y;\n"
    "    s32 z;\n"
    "} Vector3;\n"
    "typedef struct {\n"
    "    char pad0[0x20];\n"
    "    Vector3 position;\n"
    "    s16 angle;\n"
    "} Actor;\n")


def test_type_sizes_resolves_a_struct_declared_in_the_same_file():
    sizes = diffrepair.type_sizes(NESTED)
    assert sizes["Vector3"] == 12
    assert sizes["Actor"] == 48       # 0x20 pad + 12 + 2, aligned to 4


def test_a_nested_struct_no_longer_shifts_every_later_field():
    """The member used to be skipped WITHOUT advancing the cursor, so `angle`
    was reported at 0x20 instead of 0x2c and every layout proposal that
    depended on it was silently declined."""
    region = diffrepair._struct_regions(NESTED)[1]
    offs = [o for _m, o, _s in diffrepair.region_fields(NESTED, region)]
    assert offs == [0, 0x20, 0x2c]


def test_region_fields_declines_when_a_member_type_is_unresolvable():
    """Unknown is the default: a wrong offset is worse than no offset because
    nothing downstream can falsify it."""
    code = ("typedef struct {\n"
            "    char pad0[0x10];\n"
            "    SomeUndeclaredType thing;\n"
            "    s16 angle;\n"
            "} Actor;\n")
    region = diffrepair._struct_regions(code)[0]
    assert diffrepair.region_fields(code, region) == []


def test_delta_clusters_separate_two_objects_sharing_one_register():
    diff = _mdiff([
        ("lw t0,0x44(s0)", "lw t0,0x30(s0)"),
        ("lw t1,0x48(s0)", "lw t1,0x34(s0)"),
        ("lw t2,0x74(s0)", "lw t2,0x6c(s0)"),
        ("lw t3,0x7c(s0)", "lw t3,0x74(s0)"),
    ])
    clusters = dict(diffrepair.delta_clusters(diff))
    assert clusters["s0+0x14"] == {0x30: 0x44, 0x34: 0x48}
    assert clusters["s0+0x8"] == {0x6c: 0x74, 0x74: 0x7c}


def test_delta_clusters_ignore_a_negative_shift():
    """Padding moves fields later only; a negative shift is a width fix."""
    diff = _mdiff([
        ("lw t0,0x1c(s0)", "lw t0,0x20(s0)"),
        ("lw t1,0x20(s0)", "lw t1,0x24(s0)"),
        ("lw t2,0x74(s0)", "lw t2,0x6c(s0)"),
        ("lw t3,0x7c(s0)", "lw t3,0x74(s0)"),
    ])
    assert all(not b.endswith("-0x4")
               for b, _m in diffrepair.delta_clusters(diff))


def test_reorder_sets_keeps_what_constraint_sets_must_drop():
    diff = _mdiff([
        ("lh t0,0x302(s0)", "lh t0,0x78(s0)"),
        ("lw t1,0x2fc(s0)", "lw t1,0x7c(s0)"),
    ])
    assert diffrepair.constraint_sets(diff) == []
    sets = diffrepair.reorder_sets(diff)
    assert len(sets) == 1 and sets[0][1] == {0x78: 0x302, 0x7c: 0x2fc}


# ------------------------------------------------------------- signedness

def test_a_signedness_only_difference_is_a_retype():
    """lb and lbu are both one byte, so equal-width used to end the check and
    every signedness fault was discarded silently."""
    w = diffrepair.width_constraints(_d([("lb t0,0x10(a0)", "lbu t0,0x10(a0)")]))
    assert w == {0x10: (1, False)}


def test_signedness_retype_flips_the_declared_type():
    body = ("typedef struct {\n"
            "    char pad0[0x10];\n"
            "    u8 flags;\n"
            "} T;\n")
    out, changed = diffrepair.apply_widths(body, {0x10: (1, False)})
    assert changed and "s8 flags;" in out


def test_matching_signedness_proposes_nothing():
    w = diffrepair.width_constraints(
        _d([("lbu t0,0x10(a0)", "lbu t0,0x10(a0)")]))
    assert w == {}


def test_a_store_still_carries_no_signedness():
    """sb says four bytes moved, not whether the field is signed."""
    w = diffrepair.width_constraints(_d([("sw t1,0x1c(t2)", "sb t1,0x1c(t2)")]))
    assert w.get(0x1c) == (4, None)


def test_padding_insertion_is_idempotent():
    """`Duplicate member 'dpad00'` was 143 attempts -- 3.6% of every compile
    failure ever logged -- and every one was self-inflicted: the padding name
    is derived from the position it fills, so re-running the pass on an
    already-repaired body recomputed the same position and inserted a second
    `char dpad00[0x2];` beside the first."""
    from solver import diffrepair
    assert diffrepair._already_declared("    char dpad00[0x2];\n", "dpad00")
    assert not diffrepair._already_declared("    char dpad04[0x2];\n", "dpad00")

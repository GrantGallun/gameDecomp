"""Tests for diff-directed layout repair.

This pass rewrites a candidate's struct from the oracle's diff, so the tests
are mostly about refusing to act on an unreliable reading. A wrong repair here
does not just fail -- it can move every subsequent field.
"""

from solver import diffrepair


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


def test_drifted_pairing_is_rejected():
    """Non-monotonic constraints from one base mean the diff lines drifted."""
    m, dropped = diffrepair.constraints(_d([
        ("lw v0,0x20(a0)", "lw v0,0(a0)"),
        ("lw v1,0x10(a0)", "lw v1,4(a0)"),
    ]))
    assert m == {} and dropped == {"a0": "non-monotonic"}


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

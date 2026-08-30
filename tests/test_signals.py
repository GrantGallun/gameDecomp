"""Tests for structural candidate signals and Pareto selection."""

from solver import signals


def _d(pairs):
    """Build a diff from (expected, produced) instruction pairs."""
    out = []
    for a, b in pairs:
        if a:
            out.append("-" + a)
        if b:
            out.append("+" + b)
    return "\n".join(out)


def test_offset_difference_is_layout_not_structural():
    s = signals.analyse(_d([("lw v0,0x28(a0)", "lw v0,0x20(a0)")]))
    assert s.layout == 1 and s.structural == 0


def test_width_difference_is_layout():
    s = signals.analyse(_d([("lw v0,0x10(a0)", "lh v0,0x10(a0)")]))
    assert s.layout == 1 and s.structural == 0


def test_same_access_different_register_is_regalloc():
    s = signals.analyse(_d([("lh a2,0x26(s0)", "lh a1,0x26(s0)")]))
    assert s.regalloc == 1 and s.structural == 0 and s.layout == 0


def test_branch_shape_is_structural():
    s = signals.analyse(_d([("b 390", "beqz v1,390")]))
    assert s.structural == 1


def test_same_branch_different_target_is_structural():
    s = signals.analyse(_d([("beq v0,v1,100", "beq v0,v1,200")]))
    assert s.structural == 1


def test_relocation_mismatch_is_its_own_kind():
    s = signals.analyse(_d([("lui at,%hi(jtbl_800E08BC)",
                             "lui at,%hi(.rodata)")]))
    assert s.reloc == 1 and s.structural == 0


def test_missing_instructions_count_as_structural():
    """Register allocation cannot change how many instructions exist."""
    s = signals.analyse("-nop\n-addu t0,t1,t2\n+addu t0,t1,t2")
    assert s.instr_delta == 1
    assert s.structural >= 1


def test_repairable_and_unrepairable_split():
    s = signals.analyse(_d([
        ("lw v0,0x28(a0)", "lw v0,0x20(a0)"),
        ("b 390", "beqz v1,390"),
    ]))
    assert s.repairable == 1 and s.unrepairable == 1


# ------------------------------------------------------------ Pareto

def test_lower_score_survives_when_structurally_cleaner():
    """The whole point: a 90 with no structural faults beats a 96 with them."""
    hi = signals.Signals(score=96.0, structural=8, layout=0)
    lo = signals.Signals(score=90.0, structural=0, layout=9)
    keep = signals.pareto([("hi", hi), ("lo", lo)])
    assert ("lo", lo) in keep
    assert keep[0][0] == "lo"          # ranked first: fewer structural faults


def test_dominated_candidate_is_dropped():
    good = signals.Signals(score=96.0, structural=0, layout=1)
    bad = signals.Signals(score=90.0, structural=3, layout=4)
    keep = signals.pareto([("good", good), ("bad", bad)])
    assert [k for k, _ in keep] == ["good"]


def test_identical_candidates_both_survive():
    a = signals.Signals(score=90.0, structural=1, layout=1)
    b = signals.Signals(score=90.0, structural=1, layout=1)
    assert len(signals.pareto([("a", a), ("b", b)])) == 2


def test_non_compiling_candidate_is_inert():
    s = signals.analyse("", compiled=False)
    assert s.structural == 0 and s.diff_lines == 0


def test_offset_and_width_are_counted_separately():
    """repad can move a field; it cannot retype one.

    Lumping them hid why three functions with zero structural faults still
    could not be finished deterministically.
    """
    s = signals.analyse(_d([
        ("lw v0,0x28(a0)", "lw v0,0x20(a0)"),     # offset -- repairable
        ("sw t1,0x1c(t2)", "sb t1,0x1c(t2)"),     # width  -- not by repad
    ]))
    assert s.offset == 1 and s.width == 1 and s.layout == 2

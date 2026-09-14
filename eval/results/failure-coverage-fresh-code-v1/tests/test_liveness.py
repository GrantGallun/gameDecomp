"""Tests for backward liveness / register pressure at call sites."""

from solver import liveness

HOLD = ("lui t6,0x1\n"
        "addiu a0,t6,4\n"
        "jal something\n"
        "nop\n"
        "addu v0,a0,t6\n"
        "jr ra\n"
        "nop\n")

RECOMPUTE = ("lui t6,0x1\n"
             "addiu a0,t6,4\n"
             "jal something\n"
             "nop\n"
             "lui t6,0x1\n"
             "addu v0,a0,t6\n"
             "jr ra\n"
             "nop\n")


def test_value_held_across_a_call_is_live():
    _per, calls = liveness.analyse(HOLD)
    assert len(calls) == 1
    assert sorted(calls[0].live) == ["a0", "t6"]


def test_recomputing_after_the_call_lowers_pressure():
    """This is the whole signal: one fewer value surviving the call."""
    assert liveness.pressure_profile(HOLD) == [2]
    assert liveness.pressure_profile(RECOMPUTE) == [1]


def test_temporaries_above_t5_are_counted():
    """uopt's colour pool stops at t5; caller-saved registers do not, and
    filtering on the pool silently under-reported pressure."""
    assert "t6" in liveness.ALLOCATABLE and "t9" in liveness.ALLOCATABLE


def test_a_store_reads_its_first_operand():
    """Treating sw's first operand as a def would make stored values dead."""
    asm = ("lui t0,0x1\n"
           "sw t0,0(a0)\n"
           "jal f\n"
           "nop\n"
           "sw t0,4(a0)\n"
           "jr ra\n"
           "nop\n")
    _per, calls = liveness.analyse(asm)
    assert "t0" in calls[0].live


def test_no_calls_means_no_pressure_points():
    assert liveness.pressure_profile("addu v0,a0,a1\njr ra\nnop\n") == []


def test_empty_input_is_handled():
    assert liveness.analyse("") == ({}, [])

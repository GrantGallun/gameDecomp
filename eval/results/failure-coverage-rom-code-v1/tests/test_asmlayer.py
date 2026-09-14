"""Tests for the pre-as1 assembly layer.

These are mostly about the parsing, because the compile itself needs the real
toolchain and is exercised by eval/slayer.py.
"""

from solver import asmlayer

SAMPLE = """\t.verstamp\t3 19
\t.option\tpic2
\t.text
\t.file\t2 "/tmp/probe.c"
\t.loc\t2 1
 #   1\tint f(int a, int b){ return a*b + 3; }
f:
\tmul\t$2, $4, $5
\taddu\t$2, $2, 3
\tj\t$31
"""


def test_line_records_and_echoed_source_are_dropped():
    """`.loc` and the echoed source line move with formatting, not codegen."""
    ins = asmlayer.instructions(SAMPLE)
    assert not any(".loc" in l for l in ins)
    assert not any("int f(int a" in l for l in ins)
    assert "mul\t$2, $4, $5" in ins


def test_decisions_extract_opcode_and_registers():
    dec = asmlayer.decisions(SAMPLE)
    assert ("mul", ("2", "4", "5")) in dec
    assert ("addu", ("2", "2")) in dec


def test_directives_are_not_decisions():
    dec = asmlayer.decisions(SAMPLE)
    assert not any(op.startswith(".") for op, _ in dec)


def test_register_changes_reports_only_moved_registers():
    a = "f:\n\tmul\t$2, $4, $5\n\taddu\t$2, $2, 3\n"
    b = "f:\n\tmul\t$3, $4, $5\n\taddu\t$3, $3, 3\n"
    changed = asmlayer.register_changes(a, b)
    assert [op for op, _x, _y in changed] == ["mul", "addu"]
    assert changed[0][1] == ("2", "4", "5") and changed[0][2] == ("3", "4", "5")


def test_identical_input_reports_no_change():
    assert asmlayer.register_changes(SAMPLE, SAMPLE) == []
    assert asmlayer.diff(SAMPLE, SAMPLE) == []


def test_flags_match_the_workspace_build():
    """A different translation unit is a different program."""
    for flag in ("-mips1", "-non_shared", "-Wab,-r4300_mul", "-Xcpluscomm"):
        assert flag in asmlayer.CFLAGS
    assert "-DNDEBUG" in asmlayer.C_DEFINES
    assert "-c" not in asmlayer.CFLAGS      # -S replaces it

"""Tests for the region/lexicon/sequential architecture.

Written after an external review pointed out that solver/shifts.py had grown to
~500 lines with zero test coverage, while "32 tests pass" was reported as if it
covered the new code. It did not.

Every test here corresponds to a bug that actually occurred, or to a claim the
code makes that could silently stop being true.
"""

import re

import pytest

from solver import shifts

# A synthetic function with a real backward branch, in the same textual form as
# the target asm: /* offset vram encoding */ then mnemonic and operands.
LOOP_ASM = "\n".join(
    ["glabel testFunc"]
    + [f"    /* {i:05X} 8004{i:04X} 00000000 */  addiu      $t0, $t0, 1"
       for i in range(40)]
    + ["  .L80047C00:"]
    + [f"    /* {i:05X} 8004{i:04X} 00000000 */  addu       $t1, $t1, $t0"
       for i in range(40, 70)]
    + ["    /* 00070 80047C46 00000000 */  bne        $t1, $zero, .L80047C00",
       "    /* 00071 80047C47 00000000 */  nop",
       "    /* 00072 80047C48 00000000 */  jr         $ra"])

REAL_ASM = """\
glabel drawThing
    /* 48784 80047B84 0005C400 */  sll        $t8, $a1, 16
    /* 4878C 80047B8C 27BDFF98 */  addiu      $sp, $sp, -0x68
    /* 48790 80047B90 3C0A8015 */  lui        $t2, %hi(gViewportWidth)
    /* 48794 80047B94 854A660A */  lh         $t2, %lo(gViewportWidth)($t2)
    /* 487A8 80047BA8 AFB00008 */  sw         $s0, 0x8($sp)
    /* 487AC 80047BAC 8FB00008 */  lw         $s0, 0x8($sp)
    /* 487B0 80047BB0 8C820024 */  lw         $v0, 0x24($s0)
    /* 487B4 80047BB4 0C001234 */  jal        someCallee
    /* 487B8 80047BB8 03E00008 */  jr         $ra
"""


def test_strip_asm_drops_comments_but_no_instructions():
    """strip_asm is claimed lossless. Every mnemonic must survive."""
    stripped = shifts.strip_asm(REAL_ASM)
    assert "/*" not in stripped and "80047B84" not in stripped
    for mnemonic in ("sll", "addiu", "lui", "lh", "sw", "lw", "jal", "jr"):
        assert mnemonic in stripped, f"{mnemonic} lost by strip_asm"
    # same number of non-blank lines in and out
    assert len([l for l in REAL_ASM.splitlines() if l.strip()]) == \
        len(stripped.splitlines())


def test_loop_spans_finds_backward_branch():
    lines = [l for l in LOOP_ASM.splitlines() if l.strip()]
    spans = shifts.loop_spans(lines)
    assert spans, "backward branch not detected"
    start, end = spans[0]
    assert start < end


def test_split_regions_never_splits_a_loop():
    """The entry-1 guarantee. Regressing this silently breaks slice-wise C."""
    lines = [l for l in LOOP_ASM.splitlines() if l.strip()]
    spans = shifts.loop_spans(lines)
    regions = shifts.split_regions(LOOP_ASM, target_size=20)
    region_of = {}
    for r_i, r in enumerate(regions):
        for i in range(r.start, r.end + 1):
            region_of[i] = r_i
    for s, e in spans:
        assert region_of.get(s) == region_of.get(e), \
            "a loop was split across a region boundary"


def test_split_regions_covers_every_line_exactly_once():
    """A dropped line would silently delete code from the reconstruction."""
    regions = shifts.split_regions(LOOP_ASM, target_size=20)
    rebuilt = "\n".join(r.text for r in regions).splitlines()
    original = [l for l in LOOP_ASM.splitlines() if l.strip()]
    assert rebuilt == original


def test_lexicon_reports_frame_and_globals_with_widths():
    lex = shifts.lexicon(REAL_ASM)
    assert "0x68" in lex, "frame size missing"
    assert "gViewportWidth" in lex, "referenced global missing"
    assert "2b" in lex, "lh should be recorded as a 2-byte access"
    assert "someCallee" in lex, "call target missing"
    assert "0x24" in lex, "struct field offset missing"


def test_lexicon_does_not_double_count_save_slots():
    """sw saves and lw restores the SAME slot; listing it twice implies a
    frame twice as busy. This was a real bug caught by ground truth."""
    lex = shifts.lexicon(REAL_ASM)
    saves = re.search(r"saved: ([^\n]+)", lex)
    assert saves, "save slots missing"
    entries = [e.strip() for e in saves.group(1).split(",")]
    assert len(entries) == len(set(entries)), f"duplicate save slots: {entries}"


def test_lexicon_invents_nothing():
    """Every symbol in the lexicon must appear in the assembly it came from."""
    lex = shifts.lexicon(REAL_ASM)
    for sym in re.findall(r"\b(g[A-Z]\w+|someCallee)\b", lex):
        assert sym in REAL_ASM, f"lexicon invented {sym}"


def test_slice_fences_parse_when_present():
    text = "```decls\nint x;\n```\n```stmts\nx = 1;\n```"
    assert shifts.DECLS_RE.search(text).group(1).strip() == "int x;"
    assert shifts.STMTS_RE.search(text).group(1).strip() == "x = 1;"


def test_truncated_reasoning_trace_yields_no_fences():
    """The entry-2 bug: the model spends its whole budget reasoning and never
    writes the fenced answer. That must be detectable, not silently empty."""
    trace = ("We need to translate instructions 0-47 into C. Let's decode.\n"
             "Function prologue: stack frame 0x30 bytes. So locals at offsets.")
    assert shifts.STMTS_RE.search(trace) is None
    assert shifts.DECLS_RE.search(trace) is None


@pytest.mark.parametrize("size", [20, 45, 80])
def test_regions_are_bounded_except_around_loops(size):
    """Regions may exceed target_size only to keep a loop whole."""
    lines = [l for l in LOOP_ASM.splitlines() if l.strip()]
    spans = shifts.loop_spans(lines)
    for r in shifts.split_regions(LOOP_ASM, target_size=size):
        n = len(r.text.splitlines())
        if n > size * 2:
            assert any(r.start <= s and e <= r.end for s, e in spans), \
                f"region {r.index} is oversized without containing a loop"

"""The discriminator must split the two real cases, and it must do so for the stated reason.

Both cases are real residuals from kb-sbk1.sqlite and both are recorded in
patterns/catalog.py as `ordering-diff-conflates-causes`. A test that only checked "Fstop is order"
would pass on a coin flip; the pair is what makes it a discriminator.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import ordering                                          # noqa: E402

# Fstop, attempt 46494: four DISTINCT stores permuted. Registers are identical on both sides; the
# offsets move. Statement order is the cause and reordering the C closed it exactly.
FSTOP = """--- target_object_dump_normalized.s
+++ Fstop_object_dump_normalized.s
@@ -1,8 +1,8 @@
  sw    a1,4(sp)
-sw    zero,0x60(a0)
-sw    zero,0x68(a0)
+sw    zero,0x14(a0)
  sw    zero,0x54(a0)
+sw    zero,0x60(a0)
  sh    zero,0xbe(a0)
-sw    zero,0x14(a0)
+sw    zero,0x68(a0)
  jr    ra
  move    v0,zero
"""

# drawCharacterSelectCoursePreviewPanel8, attempt 32870: the SAME instruction with its register
# exchanged. Permuting statements cannot reach it; the naive rule application regressed the function.
SIBLING = """--- target_object_dump_normalized.s
+++ drawCharacterSelectCoursePreviewPanel8_object_dump_normalized.s
@@ -54,8 +54,8 @@
  slti    at,s0,0x10
  bnez    at,3c
  addiu    s2,s2,2
-move    s2,zero
  move    s3,zero
+move    s2,zero
  li    s0,0x80
  lh    a0,0x42(s4)
  jal     getRelocatableHeapBlockBase
"""


def test_fstop_is_statement_order():
    got = ordering.classify(FSTOP)
    assert got.name == "order", got
    assert got.differing >= 4


def test_sibling_is_register_colouring():
    got = ordering.classify(SIBLING)
    assert got.name == "colouring", got
    assert got.colouring_hunks == 1


def test_the_sibling_verdict_is_named_not_observed():
    """CORRECTION 2026-09-17: the `colouring` verdict is one of two readings of an ambiguous diff.

    `classify` is POSITIONAL, so on a hunk whose instruction multisets are equal and whose differing
    positions are register-only it cannot tell "the registers were exchanged" from "the same two
    instructions were emitted in the other order". Both instructions write the constant zero, so the
    byte sequences are the same either way. What decides it is the rest of the dump: every other use
    of s2 and s3 is byte-identical between target and candidate, so each register holds the same value
    in both and nothing was reassigned. That is a reordering, and `regalloc_signature.Report.renames`
    is the counter that says so.

    The routing is unchanged and still right -- no statement permutation closes the class (measured:
    three from `statement_order_rewrites`, seven hand-written, all byte-identical output) -- but the
    verdict must not be quoted as evidence that a register changed.
    """
    from solver import regalloc_signature

    target = "addiu    s2,s2,2\nmove    s2,zero\nmove    s3,zero\nli    s0,0x80\n"
    candidate = "addiu    s2,s2,2\nmove    s3,zero\nmove    s2,zero\nli    s0,0x80\n"
    report = regalloc_signature.compare(target, candidate)
    assert ordering.classify(SIBLING).name == "colouring"       # the positional verdict
    assert report.renames == 0 and report.reordered == 2        # but nothing changed register
    assert report.order_only is True
    # And the positional verdict costs the register signature nothing: same counts as before.
    assert report.signatures == {"saved_order": 2}


def test_register_normalisation_separates_them():
    """The reason, not just the verdict: the sibling's instructions become identical without their
    registers, and Fstop's do not."""
    assert (ordering.normalise_registers("move s2,zero")
            == ordering.normalise_registers("move s3,zero"))
    assert (ordering.normalise_registers("sw zero,0x60(a0)")
            != ordering.normalise_registers("sw zero,0x14(a0)"))


def test_an_added_instruction_is_not_a_permutation():
    diff = ("--- target\n+++ cand\n@@ -1,2 +1,3 @@\n lw t0,0(a0)\n"
            "+nop\n jr ra\n")
    assert ordering.classify(diff).name == "not-a-permutation"


def test_no_hunks_is_reported_rather_than_guessed():
    assert ordering.classify("").name == "no-hunks"
    assert ordering.classify("--- a\n+++ b\n").name == "no-hunks"


def test_a_mixed_residual_counts_as_order():
    """One register-only hunk and one real reorder means the order pass has something real to do;
    calling it colouring would suppress that work."""
    mixed = SIBLING + """@@ -80,6 +80,6 @@
  lw t0,0(a0)
-sw t1,0x24(a0)
 sw t2,0x28(a0)
+sw t1,0x24(a0)
  jr ra
"""
    assert ordering.classify(mixed).name == "order"

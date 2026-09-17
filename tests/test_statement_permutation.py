"""What a residual can and cannot tell us about statement order.

`patterns.ordering.hunk_permutation` computes the permutation a hunk STATES: `order` such that
`target == [candidate[i] for i in order]`, declining whenever the mapping is not unique.

What this file also pins is the REFUTATION that stopped the generalisation, because it is the more
valuable half. `patterns/rules.py:StoreOrderRule` applies a diff-read permutation and closed Fstop --
but it works because a STORE carries a discriminator (its base register) that identifies which
instructions belong to the statement group. A general statement carries no such marker, and a unified
diff for a permutation ALWAYS interleaves context lines, so the hunk is longer than the group:

    Fstop          8 hunk lines  vs  3-5 statements in the run
    a 3-line rotation  5 hunk lines  vs  3 statements

So `run length == hunk length` does not hold on real residuals, and applying a hunk-wide permutation to
a shorter statement run would be a guess about which context lines the run emitted. The general
transform was written, measured against exactly those two cases, and withdrawn rather than shipped as a
generator that can never fire.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import ordering                                          # noqa: E402

# Fstop's recorded residual: five DISTINCT stores permuted, with the function's argument spill as a
# context line that is NOT part of the group -- which is exactly why StoreOrderRule filters by base.
FSTOP_DIFF = """--- target_object_dump_normalized.s
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

# A rotation of three independent non-store statements: two swaps, so NO single adjacent swap is the
# answer. This is the case that motivated generalising past stores.
ROTATION_DIFF = """--- target_object_dump_normalized.s
+++ candidate_object_dump_normalized.s
@@ -1,6 +1,6 @@
   lw    v0,0(a0)
-addu  t0,a1,a2
-addu  t1,a2,a3
 addu  t2,a3,a1
+addu  t0,a1,a2
+addu  t1,a2,a3
   jr    ra
   nop
"""


def test_the_rotation_is_read_as_a_composition():
    """The permutation is over the WHOLE hunk, because a context line is appended to BOTH sides.

    That is what makes a reorder visible as a permutation rather than as an add/remove pair, and it is
    also the reason a hunk-wide permutation cannot be applied to a shorter statement run.
    """
    order = ordering.hunk_permutation(ROTATION_DIFF)
    target = ["lw v0,0(a0)", "addu t0,a1,a2", "addu t1,a2,a3", "addu t2,a3,a1", "jr ra", "nop"]
    candidate = ["lw v0,0(a0)", "addu t2,a3,a1", "addu t0,a1,a2", "addu t1,a2,a3", "jr ra", "nop"]
    assert order == [0, 2, 3, 1, 4, 5]
    assert [candidate[i] for i in order] == target


def test_fstop_is_a_permutation_too_but_the_hunk_is_longer_than_the_group():
    """The measurement that refuted the generalisation, asserted rather than narrated."""
    order = ordering.hunk_permutation(FSTOP_DIFF)
    assert order is not None
    hunk_lines = len([l for l in FSTOP_DIFF.splitlines() if l[:1] in (" ", "-", "+")
                      and not l.startswith(("---", "+++"))])
    assert hunk_lines == 11, hunk_lines
    # Fstop's own group is the stores through one base. A run of simple statements in a source body is
    # at most the number of statements the author wrote, and no rule can know which context lines the
    # run emitted -- so `len(order)` and the run length do not correspond in general.
    assert len(order) == 8 > 3


def test_it_declines_where_a_guess_would_be_a_fabrication():
    two_hunks = FSTOP_DIFF + "@@ -20,4 +20,4 @@\n-a\n+b\n c\n"
    assert ordering.hunk_permutation(two_hunks) is None

    inserted = ("--- t\n+++ c\n@@ -1,3 +1,4 @@\n lw v0,0(a0)\n+nop\n"
                " addu t0,a1,a2\n addu t1,a2,a3\n")
    assert ordering.hunk_permutation(inserted) is None

    changed = ("--- t\n+++ c\n@@ -1,3 +1,3 @@\n lw v0,0(a0)\n-addu t0,a1,a2\n+subu t0,a1,a2\n"
               " addu t1,a2,a3\n")
    assert ordering.hunk_permutation(changed) is None

    repeated = "--- t\n+++ c\n@@ -1,4 +1,4 @@\n nop\n-nop\n nop\n nop\n"
    assert ordering.hunk_permutation(repeated) is None

    already_ordered = ("--- t\n+++ c\n@@ -1,4 +1,4 @@\n lw v0,0(a0)\n addu t0,a1,a2\n"
                       " addu t1,a2,a3\n nop\n")
    assert ordering.hunk_permutation(already_ordered) is None

    assert ordering.hunk_permutation("") is None
    assert ordering.hunk_permutation("no hunks here\n") is None


def test_the_general_generator_is_not_shipped():
    """It was written, measured against both cases above, and withdrawn.

    Shipping a generator whose precondition cannot hold on a real residual is worse than not shipping
    one: it would be the silent-decline shape this project keeps catching, and it would sit in the
    search's family list looking like coverage.
    """
    from solver import rewrites
    assert not hasattr(rewrites, "statement_permutation_rewrites")

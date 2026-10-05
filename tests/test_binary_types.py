"""Placeholder routing: which evidence source owns each `?`, and does the local derivation fire.

The motivating residual is a REAL draft, not a synthetic one: `tests/fixtures` do not exist for this, so
the tests build the exact shapes m2c emitted in this repo's own drafts -- `guMtxF2L` (a function whose
return type was unknown), `D_245A80` (a global), `sp18` (a stack local) -- and check that the classifier
sends each to the right owner. A test that only checked "declines when there is no placeholder" would
pass on a module that never fires, which is the failure mode this project keeps re-learning.
"""
from __future__ import annotations

from eval import binary_types as bt


# The shape of a real target: a local at 0x18(sp) loaded as a word, then a word loaded through it.
TARGET_WITH_POINTER_LOCAL = """
    addiu   sp,sp,-0x20
    lw      t0,0x18(sp)
    lw      t1,0x4(t0)
    sw      t1,0x18(sp)
"""

# A local accessed at one width, distinguishable signedness.
TARGET_WITH_SIGNED_BYTE = """
    lbu     v0,0x30(sp)
    bne     v0,zero,label
"""


def test_the_classifier_routes_each_real_placeholder_to_its_owner():
    """This is the measured distribution: symbols dominate, locals are the minority. Function-versus-
    global comes from the target's own call list -- the first version used the first letter's case and
    misrouted `pimgr_bss_01B0`, a bss symbol, to the function resolver."""
    groups = bt.classify(["guMtxF2L", "sp18", "D_245A80", "pimgr_bss_01B0", "alLoadParam"],
                         asm="    jal     guMtxF2L\n    jal     alLoadParam\n")
    assert groups["local"] == ["sp18"]
    assert groups["global"] == ["D_245A80", "pimgr_bss_01B0"]
    assert groups["function"] == ["guMtxF2L", "alLoadParam"]


def test_the_local_derivation_FIRES_on_its_motivating_residual():
    """A pointer local: the value is loaded from the stack and then used as an address base. That is
    the shape that produces `Selector requires struct/union pointer as left hand side` when it is
    defaulted to `s32`, which is the dominant error after the placeholder itself is resolved."""
    result = bt.derive(TARGET_WITH_POINTER_LOCAL, "sp18")
    assert result.basis == "derived", result.reason
    assert result.pointer is True and result.type.endswith("*")
    assert "0x18(sp)" in result.located_at or result.located_at


def test_signedness_is_recorded_as_unconstrained_when_the_opcode_cannot_distinguish_it():
    """`lw`/`sw` say nothing about signedness. Returning `s32` is a default, and the receipt has to say
    so -- an unconstrained choice presented as a binary fact is exactly the kind of inference this
    project bans."""
    result = bt.derive(TARGET_WITH_POINTER_LOCAL, "sp18")
    assert "unconstrained" in result.signedness
    unsigned = bt.derive(TARGET_WITH_SIGNED_BYTE, "sp30")
    assert unsigned.basis == "derived" and unsigned.type == "u8" and unsigned.signedness == "unsigned"


def test_it_declines_rather_than_inventing_a_type():
    assert bt.derive(TARGET_WITH_POINTER_LOCAL, "sp40").basis == "none"      # never accessed
    assert bt.derive(TARGET_WITH_POINTER_LOCAL, "guMtxF2L").basis == "none"  # not a location at all
    conflicting = bt.derive("lw t0,0x18(sp)\n    lbu t1,0x18(sp)\n", "sp18")
    assert conflicting.basis == "none" and "conflicting" in conflicting.reason


def test_types_for_only_returns_derived_names_so_defaults_stay_visible():
    widths, receipt = bt.types_for(TARGET_WITH_POINTER_LOCAL, ["sp18", "guMtxF2L"])
    assert widths == {"sp18": "s32 *"}          # the symbol is left out on purpose
    assert {row["name"]: row["basis"] for row in receipt} == {"sp18": "derived", "guMtxF2L": "none"}

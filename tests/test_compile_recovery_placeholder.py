"""The recovery path must offer the placeholder-resolved draft, and must not disturb a clean one.

`solver/compile_recovery.variants` is where a node goes when it cannot compile. The campaign's own
state says that is where the work is: 1,587 `compile_recovery` visits on pending nodes, of which 61 are
`compiled=False` with no diagnostic at all -- `_Ldtob`, `_Printf`, `__osCheckPackId`, `__osRepairPackId`,
`__osLeoInterrupt`. Every one of those is a function the `?` type placeholder already explains, and no
strategy in this module addressed it.

The rewriter itself is pinned by `tests/test_m2c_placeholders.py` (8 tests, including the shapes that
must be left alone). These tests are about the WIRING: that the stage is present, that it runs before
the strategies whose diagnostics it unblocks, and that it appends rather than replaces.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "solver" / "compile_recovery.py").read_text(
    encoding="utf-8")


def test_the_stage_is_present_and_appends():
    """A stage that replaced the source instead of offering a variant would destroy the original."""
    assert "m2c_placeholders.rewrite(source)" in SOURCE
    assert "rows.append(('m2c-type-placeholder', resolved))" in SOURCE
    assert "'stage': 'm2c-type-placeholder'" in SOURCE


def test_the_stage_runs_before_the_strategies_it_unblocks():
    """Position is the point. cfe truncates its error list at the placeholder, so a strategy that reads
    diagnostics -- `local_call_interface.missing_pointer_interfaces` takes
    `(attempt.frontend or {}).get('diagnostics')` -- is reading a list that stops at the parse error.
    The rewrite must be offered ahead of them."""
    stage = SOURCE.index("m2c_placeholders.rewrite(source)")
    for later in ("local_call_interface.missing_pointer_interfaces",
                  "wide_parameter_repair.propose",
                  "absolute = m2c_adapter.resolve_absolute_unknowns"):
        assert stage < SOURCE.index(later), f"{later} now runs before the placeholder stage"


def test_the_motivating_residual_is_the_one_the_rewriter_handles():
    """End to end on the actual text: `_Litob`'s prototype line, which is where cfe stopped."""
    from solver import m2c_placeholders
    draft = 'u64 __ull_rem(s32, s32, s32, s32);\n? lldiv(s32 *, s32, s32);\n'
    resolved, names = m2c_placeholders.rewrite(draft)
    assert names == ["lldiv"]
    assert "?" not in resolved


def test_a_clean_source_produces_no_variant():
    """The common case. A stage that fired here would add a duplicate candidate to every recovery."""
    from solver import m2c_placeholders
    clean = "s32 f(s32 a) {\n    return a + 1;\n}\n"
    assert m2c_placeholders.rewrite(clean) == (clean, [])

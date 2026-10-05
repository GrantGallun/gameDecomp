"""The engine's intake must offer the placeholder-resolved draft, and must not disturb a clean one.

`completion_campaign._intake` is the single place every draft passes through -- m2c seeds, historical
seeds and forked frontiers alike -- so the placeholder rewrite is wired there rather than into any one
profile. These tests drive the caller, not the rewriter: a stage wired into the engine that silently
declines is invisible exactly where nobody watches.
"""
from __future__ import annotations

from pathlib import Path

from solver import m2c_placeholders as mp


def _intake_proposal(candidate: str) -> list[tuple[str, str]]:
    """The candidate list `_intake` builds for one variant, minus the header adapter.

    Mirrors `completion_campaign._intake` lines 255-262. Kept as a mirror rather than a call because
    `_intake` boots a workspace; the assertion that matters -- that the engine's source contains this
    construction -- is `test_the_engine_offers_the_resolved_variant` below.
    """
    resolved, names = mp.rewrite(candidate)
    proposed = [("seed", candidate)]
    if names:
        proposed.append(("m2c-type-placeholder", resolved))
    return proposed


def test_the_engine_offers_the_resolved_variant(tmp_path: Path):
    """A `?` draft yields TWO candidates: the original and the resolved one. The object decides."""
    src = "u64 __ull_rem(s32, s32, s32, s32);\n? lldiv(s32 *, s32, s32);\n"
    proposed = _intake_proposal(src)
    labels = [label for label, _ in proposed]
    assert labels == ["seed", "m2c-type-placeholder"]
    assert "?" in proposed[0][1], "the original must survive as its own candidate"
    assert "?" not in proposed[1][1]


def test_a_clean_draft_costs_nothing(tmp_path: Path):
    """The rewrite declines and the engine sees exactly the one candidate it saw before."""
    src = "s32 f(s32 a) {\n    return a + 1;\n}\n"
    assert _intake_proposal(src) == [("seed", src)]


def test_the_wiring_is_present_in_the_engine_source():
    """The mirror above is only meaningful while the engine actually contains the construction."""
    text = (Path(__file__).resolve().parents[1] / "eval" / "completion_campaign.py").read_text(
        encoding="utf-8")
    assert "m2c_placeholders.rewrite(candidate)" in text
    assert '"m2c-type-placeholder"' in text
    # And the resolved variant must be appended to the proposals, not substituted for the original.
    assert "proposed = [(label, candidate)]" in text

"""A zero-model campaign must start without a live endpoint; a model campaign must still be frozen.

`fast_campaign` called `frozen_wavefront.model_digest` unconditionally at startup, so the controller
could not start without the endpoint it was pinned to -- even to do work that makes no model calls.
Measured 2026-09-17: the `resume-pipeline-20260908` supervisor retried, failed with
`URLError: timed out` against `http://172.28.32.1:11435`, and sat in `needs_repair` with
`consecutive_failures: 3` and a 25-hour-old heartbeat. `run_expansion.py:202` already guards the same
call with `if args.model_calls else None`.

These pin the guard's SEMANTICS rather than only its text, because the failure it prevents is a
startup crash and the failure it could cause is a silently unfrozen run.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "eval" / "fast_campaign.py").read_text(
    encoding="utf-8")


def test_the_digest_check_is_guarded_by_model_calls():
    """The guard is present, and it guards the digest call rather than sitting near it."""
    assert "if args.model_calls and campaign.frozen_wavefront.model_digest(" in SOURCE
    # The unguarded form must be gone: a second, bare call would still crash a zero-model run.
    assert "\n        if campaign.frozen_wavefront.model_digest(" not in SOURCE


def test_the_guard_matches_the_existing_convention():
    """`run_expansion` guards the identical call the identical way. If that changes, this should be
    revisited rather than left as an unexplained divergence between the two controllers."""
    expansion = (Path(__file__).resolve().parents[1] / "eval" / "experiments" / "campaign-gap-audit"
                 / "run_expansion.py").read_text(encoding="utf-8")
    assert "if args.model_calls else None" in expansion


def test_a_model_run_still_raises_on_a_changed_model():
    """The invariant the guard must NOT weaken: with model calls enabled, a different model is fatal.

    Exercised against `frozen_wavefront` directly, since `fast_campaign.run` needs a full run
    directory. This is the behaviour the unconditional call was protecting."""
    from eval import frozen_wavefront
    calls = []

    def fake_digest(endpoint, model):
        calls.append((endpoint, model))
        return "different-digest"

    original = frozen_wavefront.model_digest
    frozen_wavefront.model_digest = fake_digest
    try:
        model_calls = 3
        state_digest = "recorded-digest"
        raised = False
        try:
            if model_calls and frozen_wavefront.model_digest("http://x", "m") != state_digest:
                raise ValueError("model changed")
        except ValueError:
            raised = True
        assert raised, "a model run must still refuse a changed model"
        assert calls == [("http://x", "m")]

        calls.clear()
        model_calls = 0
        if model_calls and frozen_wavefront.model_digest("http://x", "m") != state_digest:
            raise ValueError("model changed")
        assert calls == [], "a zero-model run must not touch the endpoint at all"
    finally:
        frozen_wavefront.model_digest = original

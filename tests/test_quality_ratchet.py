"""The ratchet must FIRE on a regression acceptance cannot see, and hold on a clean improvement.

MOTIVATING RESIDUAL (LOOP-6): `implicit_externs` left IDO / IDO+frontend / exact exactly flat while 12
states' final candidates gained a frontend error. Greedy adoption on a rank TIE carried them forward, and
an acceptance-only ratchet reported nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import quality_ratchet as qr                                        # noqa: E402


def _receipt(rows):
    return {"rows": [{"function": name,
                      "sequence": {"compiled": c, "frontend_passed": f, "exact": e},
                      "diagnostic_trace": [{"errors": errors}]}
                     for name, c, f, e, errors in rows]}


def test_it_fires_when_quality_drops_and_acceptance_is_flat():
    """THE MOTIVATING CASE: same acceptance, one candidate carries an extra error."""
    before = _receipt([("a", True, False, False, 1), ("b", False, False, False, 3)])
    after = _receipt([("a", True, False, False, 2), ("b", False, False, False, 3)])
    report = qr.compare(before, after)
    assert all(not v["lost"] for v in report["levels"].values()), "acceptance really is flat"
    assert report["worse"] == [("a", 1, 2)]
    assert report["holds"] is False


def test_it_fires_on_a_lost_state_even_if_errors_fall():
    before = _receipt([("a", True, True, False, 0)])
    after = _receipt([("a", False, False, False, 0)])
    report = qr.compare(before, after)
    assert report["levels"]["compiled"]["lost"] == ["a"]
    assert report["holds"] is False


def test_it_holds_on_a_clean_improvement():
    before = _receipt([("a", False, False, False, 5), ("b", True, False, False, 2)])
    after = _receipt([("a", True, False, False, 1), ("b", True, True, False, 0)])
    report = qr.compare(before, after)
    assert report["holds"] is True
    assert report["levels"]["compiled"]["gained"] == ["a"]
    assert len(report["better"]) == 2 and report["worse"] == []


def test_the_cli_exit_code_is_the_verdict(tmp_path):
    import json

    good_a = tmp_path / "a.json"; good_b = tmp_path / "b.json"; bad = tmp_path / "c.json"
    good_a.write_text(json.dumps(_receipt([("x", True, False, False, 2)])))
    good_b.write_text(json.dumps(_receipt([("x", True, False, False, 1)])))
    bad.write_text(json.dumps(_receipt([("x", True, False, False, 3)])))
    assert qr.main([str(good_a), str(good_b)]) == 0
    assert qr.main([str(good_a), str(bad)]) == 1

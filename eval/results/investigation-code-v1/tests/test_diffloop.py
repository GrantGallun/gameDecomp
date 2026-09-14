"""Diff-loop experiments must distinguish a dead arm from a failed repair."""

from eval import diffloop
from solver import workspace


def _att(score=90.0, exact=False, compiled=True):
    return workspace.Attempt(compiled, score, exact, "diff", "err", "raw")


def test_no_repair_activation_is_reported_not_applicable(monkeypatch, tmp_path):
    monkeypatch.setattr(diffloop.workspace, "score", lambda *a, **k: _att())
    monkeypatch.setattr(diffloop.diffrepair, "repair",
                        lambda src, diff: (src, False,
                                           {"constraints": 0, "dropped": 0}))
    _att_out, _src, _log, health = diffloop.run_one(
        tmp_path, "f", "int f(void);", tmp_path, verbose=False)
    assert health == {"activated": 0, "compiled_candidates": 0,
                      "broken_candidates": 0, "terminal": "not_applicable"}


def test_repair_candidates_are_logged_with_round_receipts(monkeypatch, tmp_path):
    calls = []

    def score(*args, **kwargs):
        calls.append((args, kwargs))
        return _att(score=90.0 if len(calls) == 1 else 91.0, exact=len(calls) > 1)

    monkeypatch.setattr(diffloop.workspace, "score", score)
    monkeypatch.setattr(diffloop.diffrepair, "repair",
                        lambda src, diff: (src + "\n/* fixed */", True,
                                           {"constraints": 3, "dropped": 1}))
    best, _src, _log, health = diffloop.run_one(
        tmp_path, "f", "int f(void);", tmp_path, verbose=False,
        conn="receipt-db", run_id="run-1")
    assert best.exact
    assert health["terminal"] == "exact"
    assert calls[1][1]["strategy"] == "diffrepair"
    assert calls[1][1]["extra"] == {"round": 1, "constraints": 3, "dropped": 1}
    assert calls[1][1]["conn"] == "receipt-db"


def test_regression_keeps_prior_candidate_but_records_negative_arm(monkeypatch,
                                                                   tmp_path):
    scores = iter([_att(90.0), _att(80.0)])
    monkeypatch.setattr(diffloop.workspace, "score", lambda *a, **k: next(scores))
    monkeypatch.setattr(diffloop.diffrepair, "repair",
                        lambda src, diff: (src + "x", True,
                                           {"constraints": 1, "dropped": 0}))
    best, best_src, _log, health = diffloop.run_one(
        tmp_path, "f", "original", tmp_path, verbose=False)
    assert best.score == 90.0 and best_src == "original"
    assert health["activated"] == 1
    assert health["compiled_candidates"] == 1
    assert health["terminal"] == "regressed"

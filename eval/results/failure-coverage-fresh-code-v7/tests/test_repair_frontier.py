"""Focused frontier diversity regressions."""

from solver import repair, signals, workspace


def _state(source, score, kind, structural, regalloc):
    state = repair._State(
        source, workspace.Attempt(True, score, False, "", "", ""),
        (source,), (kind,))
    state._test_signals = signals.Signals(
        score=score, compiled=True, structural=structural,
        regalloc=regalloc)
    return state


def test_frontier_keeps_fault_best_and_score_best_within_one_kind(monkeypatch):
    fault_best = _state("fault-best", 70.0, "argswap", 0, 0)
    score_best = _state("score-best", 99.0, "argswap", 3, 4)
    layout = _state("layout", 80.0, "layout", 0, 1)
    monkeypatch.setattr(repair, "_state_signals",
                        lambda state: state._test_signals)

    kept = repair._frontier([fault_best, score_best, layout], 3)
    assert {state.source for state in kept} == {
        "fault-best", "score-best", "layout"}

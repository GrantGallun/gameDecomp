"""The production repair search composes, diversifies and deduplicates."""

from pathlib import Path

from solver import repair, rewrites, workspace


def _attempt(score: float, diff: str, *, exact: bool = False):
    return workspace.Attempt(True, score, exact, diff, "", "")


def _rw(label: str, kind: str, result: str):
    return rewrites.Rewrite(label, kind, lambda _source, out=result: out)


def test_search_reaches_a_three_rewrite_solution(monkeypatch):
    """Depth two was an arbitrary ceiling; interacting fixes can need three."""
    attempts = {
        "base": _attempt(90.0, "d0"),
        "a": _attempt(80.0, "d1"),
        "ab": _attempt(79.0, "d2"),
        "abc": _attempt(100.0, "", exact=True),
    }
    proposals = {
        "d0": [_rw("A", "layout", "a")],
        "d1": [_rw("B", "immediate", "ab")],
        "d2": [_rw("C", "reloc", "abc")],
    }
    monkeypatch.setattr(repair.workspace, "score",
                        lambda _ws, _repo, _name, source, **_kw:
                        attempts[source])
    monkeypatch.setattr(repair.rewrites, "propose",
                        lambda _source, diff: list(proposals.get(diff, [])))

    att, source, log = repair.search(
        Path("repo"), "f", "base", Path("ws"), verbose=False,
        max_depth=3, beam_width=2)

    assert att.exact and source == "abc"
    assert any("EXACT depth 3: A then B then C" in line for line in log)


def test_frontier_preserves_a_lower_scoring_rewrite_kind(monkeypatch):
    """Same-kind high scores must not crowd out an interacting lower score."""
    attempts = {
        "base": _attempt(90.0, "d0"),
        "i1": _attempt(99.0, "dead"),
        "i2": _attempt(98.0, "dead"),
        "layout": _attempt(70.0, "layout-diff"),
        "exact": _attempt(100.0, "", exact=True),
    }
    proposals = {
        "d0": [
            _rw("immediate 1", "immediate", "i1"),
            _rw("immediate 2", "immediate", "i2"),
            _rw("layout", "layout", "layout"),
        ],
        "layout-diff": [_rw("finish", "reloc", "exact")],
    }
    monkeypatch.setattr(repair.workspace, "score",
                        lambda _ws, _repo, _name, source, **_kw:
                        attempts[source])
    monkeypatch.setattr(repair.rewrites, "propose",
                        lambda _source, diff: list(proposals.get(diff, [])))

    att, source, _log = repair.search(
        Path("repo"), "f", "base", Path("ws"), verbose=False,
        max_depth=2, beam_width=2)

    assert att.exact and source == "exact"


def test_search_scores_duplicate_source_only_once(monkeypatch):
    calls: list[str] = []

    def score(_ws, _repo, _name, source, **_kw):
        calls.append(source)
        return _attempt(90.0, "d0" if source == "base" else "dead")

    monkeypatch.setattr(repair.workspace, "score", score)
    monkeypatch.setattr(
        repair.rewrites, "propose",
        lambda _source, diff: ([_rw("one", "layout", "same"),
                                _rw("two", "immediate", "same")]
                               if diff == "d0" else []))

    repair.search(Path("repo"), "f", "base", Path("ws"), verbose=False)
    assert calls == ["base", "same"]


def test_statement_order_gate_is_sticky_after_the_first_move(monkeypatch):
    """A transient structural residual must not end an allocation search."""
    attempts = {
        "base": _attempt(90.0, "allocation-shaped"),
        "swap1": _attempt(80.0, "transient-structural"),
        "exact": _attempt(100.0, "", exact=True),
    }
    first = _rw("swap one", "stmtorder", "swap1")
    finish = _rw("swap two", "stmtorder", "exact")

    monkeypatch.setattr(repair.workspace, "score",
                        lambda _ws, _repo, _name, source, **_kw:
                        attempts[source])
    monkeypatch.setattr(
        repair.rewrites, "propose",
        lambda _source, diff: [first] if diff == "allocation-shaped" else [])

    def statement_order(_source, diff, *, gate=True):
        assert diff == "transient-structural" and gate is False
        return [finish]

    monkeypatch.setattr(repair.rewrites, "statement_order_rewrites",
                        statement_order)
    att, source, _log = repair.search(
        Path("repo"), "f", "base", Path("ws"), verbose=False,
        max_depth=2, beam_width=1)
    assert att.exact and source == "exact"


def test_every_depth_one_call_site_gets_one_composition_chance(monkeypatch):
    attempts = {"base": _attempt(90.0, "base-diff")}
    first = []
    for i in range(12):
        source = f"swap-{i}"
        diff = "winner" if i == 11 else f"dead-{i}"
        attempts[source] = _attempt(90.0, diff)
        # Labels are deliberately identical: source identity, not a human
        # description, decides whether two candidates are duplicates.
        first.append(_rw("swap", "argswap", source))
    attempts["exact"] = _attempt(100.0, "", exact=True)

    def propose(_source, diff):
        if diff == "base-diff":
            return list(first)
        if diff == "winner":
            return [_rw("layout", "layout", "exact")]
        return []

    monkeypatch.setattr(repair.workspace, "score",
                        lambda _ws, _repo, _name, source, **_kw:
                        attempts[source])
    monkeypatch.setattr(repair.rewrites, "propose", propose)
    att, source, _log = repair.search(
        Path("repo"), "f", "base", Path("ws"), verbose=False,
        max_depth=2, beam_width=2)
    assert att.exact and source == "exact"

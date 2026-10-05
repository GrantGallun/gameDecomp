"""solver.register_protocol: fires on its motivating residual, and never calls a known-reachable range unreachable.

Fixtures are real traces (tools/ido-trace, IDO 5.3 -O2) of real candidates:
- register_protocol/spawnEndingCreditsPhaseAdvanceSparkle: round-2 site-edit best source; the address of
  gActiveMenuTask is coloured v1 where the target has a0 (the motivating residual).
- register_protocol/stepRaceMotionLoopingAnimation and uopt/*.before: candidates later made exact
  (scalar coalescing; uopt-guided edits), so every wrong range in them is reachable by construction.
"""
from pathlib import Path

import pytest

from solver import register_protocol

FIX = Path(__file__).parent / "fixtures"


def _load(folder: Path):
    return {n: (folder / f).read_text() for n, f in (("target", "target.s"), ("candidate", "candidate.s"),
                                                      ("level5", "level5.txt"), ("level6", "level6.txt"),
                                                      ("ugen", "ugen.txt"))}


def _analyse(case, function):
    return register_protocol.analyse(case["target"], case["candidate"], case["level5"], case["level6"],
                                     case["ugen"], function)


def test_regsused_parses_call_argument_claims():
    case = _load(FIX / "register_protocol" / "spawnEndingCreditsPhaseAdvanceSparkle")
    used = register_protocol.node_regsused(case["level5"], "spawnEndingCreditsPhaseAdvanceSparkle")
    assert {3, 4, 5} <= used[0]          # createCallbackTask(a0, a1, a2) in the entry block
    assert 3 not in used.get(1, frozenset())


def test_fires_on_address_constant_selection():
    result = _analyse(_load(FIX / "register_protocol" / "spawnEndingCreditsPhaseAdvanceSparkle"),
                      "spawnEndingCreditsPhaseAdvanceSparkle")
    [wrong] = result["ranges"]
    assert (wrong["class"], wrong["actual"], wrong["desired"]) == ("selection", "v1", "a0")
    status = {l["lever"]: l["status"] for l in wrong["levers"]}
    # no call after createCallbackTask, and the target holds nothing in v1 during the range
    assert status["preference"] == "impossible" and status["forbid-lower"] == "impossible"
    assert result["verdict"] != "reachable"


def test_known_reachable_is_never_unreachable():
    # Regression: the first census called this unreachable although scalar coalescing made it exact.
    result = _analyse(_load(FIX / "register_protocol" / "stepRaceMotionLoopingAnimation"),
                      "stepRaceMotionLoopingAnimation")
    assert result["verdict"] not in ("unreachable", "declined")


@pytest.mark.parametrize("function", ["updateCharacterSelectRosterIcons", "updateRaceUiScorePopupSlideIn"])
def test_uopt_guided_befores_are_never_unreachable(function):
    base = FIX / "uopt" / function
    case = {"target": (base.parent / f"{function}.target.s").read_text(),
            "candidate": (base.parent / f"{function}.before.s").read_text(),
            "level5": (base.parent / f"{function}.before.level5.txt").read_text(),
            "level6": (base.parent / f"{function}.before.level6.txt").read_text(),
            "ugen": (base.parent / f"{function}.before.ugen.txt").read_text()}
    result = _analyse(case, function)
    assert result["verdict"] != "unreachable", result

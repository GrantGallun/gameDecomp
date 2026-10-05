"""`extern int f();` -- C89's own implicit declaration, written out -- and the three things it must refuse.

THE RESIDUAL (`wide-intake-widen3.json`): `undeclared-function` is the sole blocker in 7 states. Three
call real ROM functions that NO header declares, so `scalar_header_prototypes` cannot reach them:

    updateRaceUiResultsBannerWaitForInput   enqueueSoundEffect(0x18, 0x32);      ROM 0x80072138
    updateCharacterSelectMenu               enqueueSoundEffect(...); x3
    drawMainMenuModeSelectIcons             drawMenuFillRectangle(...); x2      ROM 0x80046748

In C89 an undeclared call already IS `extern int f();`, and IDO compiles it that way -- only the clang
check rejects it, by policy. So writing it out leaves the object unchanged by construction.

THE OTHER FOUR must not be declared, and each has a test:

    M2C_BREAK(6);                 __ll_div, __ll_mod   not a ROM function: m2c's `break` instruction
    temp_ret = __ll_mul(...);     two projectile fns   the 64-bit result is READ; `int` would truncate it
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import implicit_extern as ie                                      # noqa: E402

ROM = {"enqueueSoundEffect", "drawMenuFillRectangle", "__ll_mul", "f"}


def _diag(*names: str) -> str:
    return "".join(f"candidate.c:1:1: error: implicit declaration of function '{n}'\n" for n in names)


def test_it_fires_on_a_real_rom_function_called_for_effect():
    """THE MOTIVATING CASE: `updateRaceUiResultsBannerWaitForInput`, its single remaining error."""
    source = "void f(void) {\n    enqueueSoundEffect(0x18, 0x32);\n}\n"
    out, report = ie.propose(source, "f", _diag("enqueueSoundEffect"), ROM)
    assert out.startswith("extern int enqueueSoundEffect();\n"), out
    assert report["declared"] == ["enqueueSoundEffect"]
    # UNPROTOTYPED: no parameter type is invented, which is what keeps the object unchanged.
    assert "enqueueSoundEffect(s16" not in out and "enqueueSoundEffect(int" not in out


def test_every_call_is_checked_not_just_the_first():
    """`updateCharacterSelectMenu` calls it three times; one read of the result would disqualify it."""
    source = ("void f(void) {\n"
              "    enqueueSoundEffect(1, 0x32);\n"
              "    if (x) enqueueSoundEffect(0x19, 0x32);\n"
              "    else enqueueSoundEffect(ids[i], 0x32);\n"
              "}\n")
    out, report = ie.propose(source, "f", _diag("enqueueSoundEffect"), ROM)
    assert report["declared"] == ["enqueueSoundEffect"], report["declines"]


def test_an_m2c_macro_is_never_declared():
    """THE TRAP. `M2C_BREAK` is not in the ROM; a prototype compiles into a `jal` to nothing."""
    source = "void f(void) {\n    M2C_BREAK(6);\n}\n"
    out, report = ie.propose(source, "f", _diag("M2C_BREAK"), ROM)
    assert out == source, "not one byte"
    assert "not a function in the ROM" in report["declines"][0]["reason"]


def test_a_read_result_is_refused_because_int_would_change_the_object():
    """`temp_ret = __ll_mul(...)` reads a 64-bit return. `extern int __ll_mul();` would truncate it."""
    source = "void f(void) {\n    temp_ret = __ll_mul(a, b, c, d);\n}\n"
    out, report = ie.propose(source, "f", _diag("__ll_mul"), ROM)
    assert out == source
    assert "reads the result" in report["declines"][0]["reason"]


def test_one_reading_call_among_statement_calls_still_disqualifies():
    source = ("void f(void) {\n"
              "    enqueueSoundEffect(1, 2);\n"
              "    x = enqueueSoundEffect(3, 4);\n"
              "}\n")
    out, report = ie.propose(source, "f", _diag("enqueueSoundEffect"), ROM)
    assert out == source
    assert report["declared"] == []


def test_result_discarded_distinguishes_statement_from_expression_positions():
    assert ie.result_discarded("{ g(1); }", "g")
    assert ie.result_discarded("{ if (x) g(1); }", "g")
    assert ie.result_discarded("{ (void)g(1); }", "g")
    assert ie.result_discarded("{ } else g(1); }", "g")
    assert not ie.result_discarded("{ x = g(1); }", "g")
    assert not ie.result_discarded("{ return g(1); }", "g")
    assert not ie.result_discarded("{ h(g(1)); }", "g")
    assert not ie.result_discarded("{ y = 1 + g(1); }", "g")
    assert not ie.result_discarded("{ }", "g"), "no call at all is not 'every call discards'"


def test_a_name_already_declared_in_the_candidate_is_left_alone():
    source = "void enqueueSoundEffect(s16, s16);\nvoid f(void) {\n    enqueueSoundEffect(1, 2);\n}\n"
    out, report = ie.propose(source, "f", _diag("enqueueSoundEffect"), ROM)
    assert out == source
    assert "already declared" in report["declines"][0]["reason"]


def test_no_diagnostic_is_a_clean_no_op():
    source = "void f(void) {\n    enqueueSoundEffect(1, 2);\n}\n"
    assert ie.propose(source, "f", "", ROM)[0] == source


def test_the_action_is_WITHDRAWN_from_the_sequence_and_the_registry():
    """THE MEASUREMENT THAT WITHDREW IT (LOOP-6), pinned so the argument for it cannot quietly win again.

    The argument is good and half of it is true: `extern int f();` leaves IDO's object unchanged. The
    other half was never checked -- the project's clang policy rejects an UNPROTOTYPED declaration as
    firmly as an implicit one ("a function declaration without a prototype is deprecated"). On the
    frozen frame it fired on 16 states, ADDED that error to each (1->2, 3->4, 2->5 on the three targets),
    left acceptance flat, and on rank ties the worse candidates were adopted -- a regression in candidate
    quality that the acceptance counts could not see.
    """
    from eval import intake_runners
    from eval.intake_probe import SEQUENCE

    registry: dict = {}
    assert "eval.intake_runners.implicit_externs" not in intake_runners.register(registry)
    assert not any(a.endswith("implicit_externs") for a in SEQUENCE)

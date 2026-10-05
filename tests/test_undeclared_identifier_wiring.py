"""Clang's names reach this action through a POSITIVE WHITELIST, because merging them raw broke the ratchet.

WHAT WAS TRIED FIRST. `undeclared_identifiers_runner` takes the names it declares from
`initial_verdict['stderr']` -- cfe's text, which stops at the FIRST error. That is the same starvation
LOOP-1 fixed for `globals_variant`, and `undefined_names` already matched both wordings, so merging
clang's text in was three lines.

WHAT IT DID (`wide-intake-undeclared.json` vs `wide-intake-typedefs.json`):

    fired            ~13  ->  159 of 200
    IDO compiled      48  ->  45     4 gained, 7 LOST
    IDO + frontend    30  ->  24     1 gained, 7 LOST

WHY, straight off the lost states' receipts:

    alSynSetPan        ALFilter, temp_a0, ALParam, temp_v0, bitwise
    __osViSwapContext  OSViMode, temp_s0, __OSViContext, temp_s1, __osViNext, __osViCurr
    __osDequeueThread  OSThread, var_a2, var_a3

Type names in type position, m2c's own SSA temporaries, and `bitwise` -- the dialect spelling
`m2c_dialect` LOWERS, which is how declaring it cost `alSynSetPan`, a state this loop had already won.
cfe's truncation had been acting as an accidental filter. **A name is not a datum.**

THE GATE IS POSITIVE, not a blacklist of the breakage seen so far: m2c's own naming for a translated
global (`gFoo`/`sFoo`) and the address-named form (`D_801121E0`), whose name IS its address and which
the relocation itself carries. Both patterns are `eval/name_triage`'s, reused rather than restated.
Everything else is counted in `frontend_names` and not declared.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import intake_runners                                               # noqa: E402
from eval.tool_runners import NO_CHANGE, OK                                   # noqa: E402

CFE_SILENT = "cfe: Error: candidate.c, line 3: Syntax Error"
# Exactly the mixture that broke the frame: one real global, one address-named datum, and four things
# that are not data at all.
CLANG_NAMES = "".join(
    f"candidate.c:{i}:1: error: use of undeclared identifier '{n}'\n"
    for i, n in enumerate(
        ["gRaceSetupPlayerCountPromptText", "D_801121E0", "bitwise", "temp_a0", "ALFilter", "var_s4"], 1))
SOURCE = ("void drawRaceSetupPlayerCountPrompt(Actor *arg0) {\n"
          "    drawMenuGlyphScript(arg0->x, arg0->y, gRaceSetupPlayerCountPromptText, 0);\n"
          "}\n")


def _frontend(monkeypatch, diagnostics: str, status: str = "rejected"):
    from solver import frontend_diagnostics as fd

    monkeypatch.setattr(fd, "analyse", lambda *a, **k: {
        "status": status, "passed": False, "error_count": 6, "gates": {},
        "source_sha256": "0" * 64, "errors": [], "diagnostics": diagnostics,
        "diagnostics_truncated": False, "errors_truncated": False,
        "reason": "the checker is unavailable" if status == "unavailable" else ""})


def _context(**extra):
    context = {"candidate": SOURCE, "function": "drawRaceSetupPlayerCountPrompt",
               "repo": ".", "target": "build/src/f.o",
               "initial_verdict": {"stderr": CFE_SILENT}}
    context.update(extra)
    return context


def _capture(monkeypatch):
    seen: dict = {}

    def propose(source, function, diagnostics):
        from solver.undeclared_identifiers import undefined_names

        seen["names"] = undefined_names(diagnostics)
        return ([("undeclared-identifier-declarations", "extern u8 gX[];\n" + source)],
                {"declared": ["gX"], "declines": []})

    from solver import undeclared_identifiers

    monkeypatch.setattr(undeclared_identifiers, "propose", propose)
    return seen


def test_it_fires_on_a_global_only_the_frontend_reports(monkeypatch):
    """THE MOTIVATING RESIDUAL: cfe names nothing, clang names the datum, the action declares it."""
    _frontend(monkeypatch, CLANG_NAMES)
    seen = _capture(monkeypatch)
    result = intake_runners.undeclared_identifiers_runner(_context(), {})

    assert result["status"] == OK, result["reason"]
    assert result["changed"] is True
    assert "gRaceSetupPlayerCountPromptText" in seen["names"]
    assert result["detail"]["frontend_names"] == 6, "every name is still COUNTED"


def test_the_address_named_form_is_allowed(monkeypatch):
    """`D_801121E0`'s name IS its address, and `lui $s1, %hi(D_801121E0)` carries it in the
    relocation -- so declaring it asserts a binary fact rather than a hypothesis."""
    _frontend(monkeypatch, CLANG_NAMES)
    seen = _capture(monkeypatch)
    intake_runners.undeclared_identifiers_runner(_context(), {})
    assert "D_801121E0" in seen["names"]


def test_the_names_that_broke_the_ratchet_never_reach_the_declaring_pass(monkeypatch):
    """THE REGRESSION, pinned by name. Each of these cost a state that had already been won."""
    _frontend(monkeypatch, CLANG_NAMES)
    seen = _capture(monkeypatch)
    result = intake_runners.undeclared_identifiers_runner(_context(), {})

    for forbidden in ("bitwise", "temp_a0", "var_s4", "ALFilter"):
        assert forbidden not in seen["names"], f"{forbidden} must never be declared"
    # And the receipt states exactly what was let through, so a future widening is visible in a diff.
    assert result["detail"]["frontend_allowed"] == [
        "gRaceSetupPlayerCountPromptText", "D_801121E0"], result["detail"]["frontend_allowed"]


def test_a_dialect_spelling_is_lowered_not_declared():
    """`bitwise` belongs to `m2c_dialect`, which REWRITES it. Two owners for one name is how
    `alSynSetPan` was won and then lost in consecutive iterations."""
    from eval import name_triage

    assert not name_triage.GLOBAL_NAMED.match("bitwise")
    assert not name_triage.ADDRESS_NAMED.match("bitwise")
    assert "bitwise" in name_triage.M2C_DIALECT


def test_only_cfes_names_are_used_when_the_checker_is_unavailable(monkeypatch):
    _frontend(monkeypatch, "", status="unavailable")
    result = intake_runners.undeclared_identifiers_runner(_context(), {})
    assert result["status"] == NO_CHANGE
    assert result["detail"]["frontend_unavailable"] == "the checker is unavailable"
    assert result["detail"]["frontend_allowed"] == []


def test_a_raising_checker_does_not_take_the_action_down(monkeypatch):
    """The frontend read is an enrichment; losing it must degrade to cfe, not to a crash."""
    from solver import frontend_diagnostics as fd

    monkeypatch.setattr(fd, "analyse", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    result = intake_runners.undeclared_identifiers_runner(_context(), {})
    assert result["status"] == NO_CHANGE
    assert "OSError" in result["detail"]["frontend_unavailable"]


def test_a_context_without_repo_or_target_says_so_rather_than_failing():
    result = intake_runners.undeclared_identifiers_runner(
        {"candidate": SOURCE, "function": "f", "initial_verdict": {"stderr": CFE_SILENT}}, {})
    assert result["status"] == NO_CHANGE
    assert "only cfe" in result["detail"]["frontend_unavailable"]

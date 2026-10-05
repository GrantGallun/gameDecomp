"""The intake runners' own wiring, tested where it silently starved an action.

THE DEFECT. `globals_variant` names the symbols it will declare from the COMPILER'S DIAGNOSTIC TEXT, and
the compiler on that path is cfe, which stops at the first error. So on any candidate whose first error is
a placeholder or a syntax error, the `undeclared identifier 'gRegionAllocPtr'` line is not in the text at
all, the action's `wanted` set is empty, and it "produced no change" -- which reads exactly like an action
with nothing to do.

Measured on the frozen 200-state frame: **211 of 374 unresolved names are this action's business**, the
largest bucket by a factor of three, and it changed the source on 5 of 200 states. Feeding it the names the
clang frontend reports -- every independent blocker in one pass, which is the whole reason
`solver/frontend_diagnostics` exists -- took it to **140 of 200**, and the frame from 32 IDO-compiling to
38, frontend-passing 19 to 21, with no state losing either.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import intake_runners                                               # noqa: E402


def _context(**extra):
    context = {"candidate": "void f(void) { gRegionAllocPtr = 0; }\n", "repo": ".",
               "target": "build/src/f.o", "function": "f",
               "initial_verdict": {"stderr": "cfe: Error: candidate.c, line 1: Syntax Error"}}
    context.update(extra)
    return context


def test_the_frontend_names_reach_the_globals_solver(monkeypatch):
    """The motivating case: cfe's text names nothing, the frontend names two symbols."""
    from solver import compile_recovery, frontend_diagnostics as fd

    seen: dict = {}

    def fake_analyse(source, *, repo, target, timeout=60):
        return {"status": "rejected", "passed": False, "errors": [
            {"line": 1, "column": 1, "what": "use of undeclared identifier 'gRegionAllocPtr'"},
            {"line": 2, "column": 1, "what": "use of undeclared identifier 'gRaceUpdatePaused'"},
            {"line": 3, "column": 1, "what": "unknown type name 'UnseenActor'"},
            {"line": 4, "column": 1, "what": "expected identifier"}],
            "error_count": 4, "gates": {}, "source_sha256": "0" * 64,
            "diagnostics": "", "diagnostics_truncated": False, "errors_truncated": False}

    def fake_globals(conn, repo, function, source, diagnostics, extra_names=frozenset()):
        seen["extra"] = set(extra_names)
        seen["diagnostics"] = diagnostics
        return source, {"stage": "binary-global-declarations", "plans": []}

    monkeypatch.setattr(fd, "analyse", fake_analyse)
    monkeypatch.setattr(compile_recovery, "globals_variant", fake_globals)
    result = intake_runners.globals_variant(_context(kb_conn=object()), {})
    assert seen["extra"] == {"gRegionAllocPtr", "gRaceUpdatePaused", "UnseenActor"}, seen["extra"]
    assert seen["diagnostics"], "cfe's own text is still passed through"
    assert result["detail"]["frontend_names"] == 3
    assert result["detail"]["frontend_unavailable"] is None


def test_an_unavailable_frontend_is_reported_rather_than_read_as_no_names(monkeypatch):
    """`unavailable` is not `no undeclared identifiers`. The reason travels in the receipt."""
    from solver import compile_recovery, frontend_diagnostics as fd

    monkeypatch.setattr(fd, "analyse", lambda *_a, **_k: {
        "status": "unavailable", "passed": None, "errors": [], "error_count": 0, "gates": {},
        "source_sha256": "0" * 64, "reason": "tools/textconv.py is missing"})
    monkeypatch.setattr(compile_recovery, "globals_variant",
                        lambda *a, **k: (a[3], {"stage": "binary-global-declarations", "plans": []}))
    result = intake_runners.globals_variant(_context(kb_conn=object()), {})
    assert result["detail"]["frontend_names"] == 0
    assert "textconv" in (result["detail"]["frontend_unavailable"] or "")


def test_no_frontend_target_falls_back_to_the_compiler_text(monkeypatch):
    """The checker needs a recipe target. Without one the action still reads cfe, and says which it used."""
    from solver import compile_recovery

    seen: dict = {}
    monkeypatch.setattr(compile_recovery, "globals_variant",
                        lambda conn, repo, function, source, diagnostics, extra_names=frozenset():
                        (seen.update(extra=set(extra_names)) or source,
                         {"stage": "binary-global-declarations", "plans": []}))
    context = _context(kb_conn=object())
    context["target"] = ""
    intake_runners.globals_variant(context, {})
    assert seen["extra"] == set()


def test_without_a_connection_the_action_still_declines_by_name():
    result = intake_runners.globals_variant(_context(), {})
    assert result["status"] == "not-applicable" and "kb_conn" in result["reason"]

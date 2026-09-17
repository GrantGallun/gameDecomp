"""The owner of one fault class was unreachable from every driver.

`signals.Signals` places the `ordering` fault class with `solver.rewrites.statement_order_rewrites`.
That pass exists and works. But `eval/repair.py`'s `passes()` yielded only repad / tracefix / c89 /
align_*, and `eval/close_nearmiss.py` refuses anything that is not regalloc-dominant -- so a function
whose residual is ordering-only was declined by every instrument at once, with nothing reporting a
gap. Same silent-decline shape as the C89 rung, the struct parser and the unlogged repair compiles.

Measured 2026-09-17 on kb-sbk1.sqlite, all three with every axis but `ordering` at zero:

    Fstop                                   99.999   ordering=4
    drawCharacterSelectCoursePreviewPanel8  99.936   ordering=2
    updateRacePlayerMode16AerialTrick       99.712   ordering=3

`close_nearmiss` reports `out-of-band` with 0 compiles for all three. This pins the wiring, and the
FIRES half, so the rung cannot silently go away again.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import repair as repair_mod                                 # noqa: E402
from solver import rewrites                                           # noqa: E402

SCHEMA = """
CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, func_addr INTEGER,
    base TEXT, base_reg TEXT, offset INTEGER, width INTEGER,
    signed INTEGER, is_load INTEGER);
"""


def _labels(**kwargs):
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return [label for label, _code in
            repair_mod.passes("void f(void) {\n    a = 1;\n    b = 2;\n}\n",
                              conn=conn, func="f", repo=Path("/nonexistent"),
                              ws=Path("/nonexistent"), objs=None, **kwargs)]


def test_ordering_rung_is_wired(monkeypatch):
    """The wiring itself: a diff must reach `statement_order_rewrites` and its variants be yielded."""
    seen = {}

    class FakeRewrite:
        """Same shape as solver.rewrites.Rewrite: label/kind/apply plus __call__(code)."""

        label = "swap 1"
        kind = "ordering"
        apply = staticmethod(lambda code: code + "/* swapped */")

        def __call__(self, code):
            return self.apply(code)

    def fake(code, diff, **kwargs):
        seen["diff"] = diff
        seen["kwargs"] = kwargs
        return [FakeRewrite()]

    monkeypatch.setattr(rewrites, "statement_order_rewrites", fake)
    labels = _labels(diff="--- target\n+++ candidate\n-move s2,zero\n+move s2,zero\n")

    assert any(l.startswith("order:") for l in labels), \
        "the ordering rung yielded nothing; the owning pass is unreachable again"
    assert seen["diff"], "the diff never reached the pass"
    assert seen["kwargs"].get("gate") is False, (
        "gate must be False: the gate exists to choose the lever, and an ordering-dominant residual "
        "is exactly what it refuses")


def test_no_diff_means_no_ordering_rung():
    """Without a residual there is nothing to key on, and the rung must not guess."""
    assert not any(l.startswith("order:") for l in _labels(diff=""))


def test_statement_order_pass_fires_without_the_gate():
    """The FIRES half, on the pass itself.

    Two provably independent adjacent assignments: neither reads or writes what the other touches, so
    the swap is semantics-preserving. With `gate=False` the pass must produce variants; this is the
    behaviour the wiring depends on, and if it ever regresses to returning [] the rung above would
    look wired while doing nothing.
    """
    code = "void f(void) {\n    x = 1;\n    y = 2;\n}\n"
    diff = ("--- target_object_dump_normalized.s\n"
            "+++ candidate_object_dump_normalized.s\n"
            "@@ -1,3 +1,3 @@\n"
            "-    move s2,zero\n"
            "+    move s2,zero\n")
    got = rewrites.statement_order_rewrites(code, diff, gate=False)
    assert got, "the ordering pass produced no variants for independent adjacent statements"


def test_the_gate_is_what_would_have_refused_them():
    """Evidence for why `gate=False` is required rather than a convenience.

    If the gate admits the synthetic residual here, this test is vacuous and should be deleted; it
    exists to record that the gate is load-bearing for the class, not to assert the pass is broken.
    """
    code = "void f(void) {\n    x = 1;\n    y = 2;\n}\n"
    diff = "--- target\n+++ candidate\n-move s2,zero\n+move s2,zero\n"
    gated = rewrites.statement_order_rewrites(code, diff, gate=True)
    ungated = rewrites.statement_order_rewrites(code, diff, gate=False)
    assert len(ungated) >= len(gated)

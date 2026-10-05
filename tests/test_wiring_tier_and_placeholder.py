"""The two wirings, tested where they now run rather than where they were developed.

Both were standalone scripts for a session; putting them inside `repair_chain` and `cohort_reconcile`
means a silent decline would now be invisible in a place nobody watches. Every generator needs a test
that asserts it FIRES on its motivating residual, and these assert firing *through the caller*.
"""
from __future__ import annotations

from pathlib import Path

from eval import cohort_reconcile, zero_token_harvest as zth
from solver import m2c_placeholders as mp


# --- wiring 1: the placeholder stage inside repair_chain -------------------------------------------

class _EmptyConn:
    """`repair_chain` reaches `structgen.layout`'s query; with no rows every later stage declines, so
    the only stage that can fire is the one under test."""

    def execute(self, _sql, _params=()):
        class _Cursor(list):
            def fetchall(self):
                return list(self)
        return _Cursor()


def test_repair_chain_fires_the_placeholder_stage():
    """The motivating residual, as repair_chain sees it: `_Litob`'s prototype line."""
    draft = 'u64 __ull_rem(s32, s32, s32, s32);\n? lldiv(s32 *, s32, s32);\n'
    code, applied, _plans, _declined = zth.repair_chain(_EmptyConn(), "f", draft, set(), None, None)
    assert "m2c-placeholder" in applied
    assert "?" not in code
    assert "s32 lldiv(s32 *, s32, s32);" in code


def test_repair_chain_still_declines_on_a_clean_draft():
    """The common case, through the caller. A stage that fired here would touch every draft."""
    draft = "s32 f(s32 a) {\n    return a + 1;\n}\n"
    code, applied, _plans, _declined = zth.repair_chain(_EmptyConn(), "f", draft, set(), None, None)
    assert "m2c-placeholder" not in applied
    assert code == draft


def test_repair_chain_leaves_a_question_mark_in_a_literal_alone():
    """Now that this runs on every draft, a `?` in a string is not a type specifier."""
    draft = 'void f(void) {\n    printf("are you sure?");\n}\n'
    code, applied, _plans, _declined = zth.repair_chain(_EmptyConn(), "f", draft, set(), None, None)
    assert "m2c-placeholder" not in applied
    assert '"are you sure?"' in code


def test_rewrite_declines_on_a_literal_only_placeholder():
    src = 'void f(void) {\n    puts("why?");\n}\n'
    assert mp.rewrite(src) == (src, [])


# --- wiring 2: the tier ceiling carried into the logged strategy ------------------------------------

def test_tier_strategy_labels_a_header_assisted_source(tmp_path: Path):
    """`status.py` reads the tier from the strategy string, so the label must carry it."""
    (tmp_path / "include" / "game").mkdir(parents=True)
    (tmp_path / "include" / "game" / "t.h").write_text("typedef struct Thing {\n    s32 x;\n} Thing;\n",
                                                       encoding="utf-8")
    src = "void f(void) {\n    Thing *t;\n    t->x = 1;\n}\n"
    strategy, ceiling = cohort_reconcile.tier_strategy("cohort-reconcile:v20", tmp_path, "f", src)
    assert ceiling == "header-assisted"
    # The substring is load-bearing: `status.py` matches `like '%project-header%'`.
    assert "project-header" in strategy


def test_tier_strategy_leaves_an_independent_source_alone(tmp_path: Path):
    (tmp_path / "include").mkdir(parents=True)
    src = "void f(void) {\n    s32 *p;\n    *p = 1;\n}\n"
    strategy, ceiling = cohort_reconcile.tier_strategy("cohort-reconcile:v20", tmp_path, "f", src)
    assert ceiling == "unqualified"
    assert strategy == "cohort-reconcile:v20:source-independent"
    # Must not trip the recovered rule either.
    assert not any(k in strategy for k in ("history-recovery", "historical-provenance",
                                           "symbol-restoration"))

import json
import sqlite3

from eval import applicability_census as ac

from test_narrow_update import RANDOM as NARROW_MOTIVATING   # the real motivating residual, not a lookalike


def ledger(source):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")
    conn.execute("INSERT INTO attempts VALUES(1, ?, '', NULL)", (source,))
    return {"kb": conn}


def row(name="randomNextObject", tu="t"):
    return {"function": name, "tu": tu, "start": {"ledger": "kb", "attempt_id": 1}}


def test_census_fires_on_a_source_with_the_motivating_shape_and_declines_a_plain_one():
    fired = ac.census([row()], ledger(NARROW_MOTIVATING))
    plain = ac.census([row()], ledger("int f(int a) { return a + 1; }"))
    # the generator under census must report its own motivating residual as a fire (CLAUDE.md silent-decline rule)
    assert fired["generators"]["narrow_update"]["fires"] == 1
    assert plain["generators"]["narrow_update"]["fires"] == 0
    assert not fired["errors"].get("narrow_update")


def test_a_raising_generator_is_an_error_not_a_non_fire(monkeypatch):
    import solver.narrow_update as nu

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(nu, "variants", boom)
    report = ac.census([row()], ledger(NARROW_MOTIVATING))
    assert report["generators"]["narrow_update"]["errors"] == 1 and report["errors"]["narrow_update"]["randomNextObject"].startswith("RuntimeError")


def many(n, tus):
    """n distinct functions (names are unique in a real manifest), each a renamed copy of the motivating source."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")
    rows = []
    for i in range(n):
        name = f"fn{i}"
        conn.execute("INSERT INTO attempts VALUES(?, ?, '', NULL)", (i + 1, NARROW_MOTIVATING.replace("randomNextObject", name)))
        rows.append({"function": name, "tu": tus(i), "start": {"ledger": "kb", "attempt_id": i + 1}})
    return rows, {"kb": conn}


def test_decision_uses_rate_and_tu_spread():
    rows, led = many(20, lambda i: f"t{i}")
    assert ac.census(rows, led)["generators"]["narrow_update"]["decision"] == "build/judge"    # 100% in 20 TUs
    rows, led = many(20, lambda i: "same")
    assert ac.census(rows, led)["generators"]["narrow_update"]["decision"] != "build/judge"    # fires, but in one TU


def test_census_fires_on_branch_shape_motivating_residual_through_the_same_call_path():
    """The dev census reported branch_shape 0/95. A zero is only evidence if the same path fires on its own residual."""
    from test_branch_shape import AI, MORE_B_FRAME, O1
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")
    conn.execute("INSERT INTO attempts VALUES(1, ?, ?, ?)", (AI, MORE_B_FRAME, json.dumps({"compiler_recipe": O1})))
    report = ac.census([{"function": "__osAiDeviceBusy", "tu": "t", "start": {"ledger": "kb", "attempt_id": 1}}], {"kb": conn})
    assert report["generators"]["branch_shape"]["fires"] == 1, report["errors"]

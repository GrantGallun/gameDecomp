from pathlib import Path

from eval import allocsearch
from solver import rewrites, workspace


_ALLOC_DIFF = "\n".join([
    "--- target", "+++ candidate", "@@ -1,2 +1,2 @@",
    "-addu a2,v0,v1", "+addu a3,v0,v1",
])


def test_search_tiebreaks_equal_fault_children_by_score_and_logs_edges(
        monkeypatch):
    root = "void f(void) { x = 1; y = 2; }\n"
    rewrites_by_source = {
        root: [
            rewrites.Rewrite("first move", "stmtorder",
                             lambda _source: root + "/* first */\n"),
            rewrites.Rewrite("better move", "stmtorder",
                             lambda _source: root + "/* better */\n"),
        ]
    }
    monkeypatch.setattr(
        allocsearch.rewrites, "statement_order_rewrites",
        lambda source, _diff, gate=False: rewrites_by_source.get(source, []))
    monkeypatch.setattr(
        allocsearch.rewrites, "_allocation_shaped", lambda _diff: True)

    calls = []

    def fake_score(_ws, _repo, _name, source, **kwargs):
        calls.append((source, kwargs))
        score = 90.0
        receipt = None
        if "first" in source:
            score, receipt = 91.0, 101
        elif "better" in source:
            score, receipt = 92.0, 102
        return workspace.Attempt(True, score, False, _ALLOC_DIFF, "", "",
                                 receipt)

    monkeypatch.setattr(allocsearch.workspace, "score", fake_score)
    exact, best, used = allocsearch.search(
        Path("unused"), Path("unused"), "f", root, depth=1, budget=3,
        verbose=False, conn=object(), parent_attempt_id=42,
        run_id="alloc-order-test")

    assert exact is None and used == 3
    assert best.score == 92.0 and "better" in best.code
    child_calls = calls[1:]
    assert {call[1]["parent_attempt_id"] for call in child_calls} == {42}
    assert {call[1]["action"] for call in child_calls} == {
        "first move", "better move"}
    assert all(call[1]["run_id"] == "alloc-order-test"
               for call in child_calls)


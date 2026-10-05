"""solver.plateau_search: a two-edit key whose edits are each neutral is found; a greedy search cannot find it."""
from types import SimpleNamespace

from solver import plateau_search, site_edits

SRC = """s32 f(s32 *p, s32 b) {
    s32 t;

    t = g(b);
    return t + *p;
}
"""


def _scorer(needed):
    """Exact only when every string in `needed` is in the source; otherwise register distance 1 (neutral)."""
    calls = []

    def score(code, label, parent=None):
        calls.append(label)
        ok = all(n in code for n in needed)
        return SimpleNamespace(compiled=True, exact=ok, score=100.0 if ok else 99.0, diff="" if ok else "-x\n+y",
                               source_attribution=None, repair_complete=ok)
    return score, calls


def test_two_neutral_edits_together_are_found(monkeypatch):
    # the key: the call inlined into its only use AND the operands swapped (loadRace... shape, synthetic here)
    monkeypatch.setattr(site_edits, "gradient", lambda a: (0, 0, 0) if a.exact else (0, 0, 1))
    from solver import workspace
    monkeypatch.setattr(workspace, "repair_complete", lambda a: a.exact)
    kids = {
        SRC: [("inline t", "inline_temp", SRC.replace("    t = g(b);\n    return t + *p;", "    return g(b) + *p;")),
              ("swap", "commutative", SRC.replace("return t + *p;", "return *p + t;"))],
    }
    inlined = kids[SRC][0][2]
    kids[inlined] = [("swap", "commutative", inlined.replace("return g(b) + *p;", "return *p + g(b);"))]
    monkeypatch.setattr(plateau_search, "_children", lambda code, fn, a, mined=True, llm_repo=None: kids.get(code, []))
    monkeypatch.setattr(site_edits, "site_lines", lambda diff, attr: ({}, "none"))
    score, calls = _scorer(["return *p + g(b);"])
    out = plateau_search.search(score, SRC, "f", budget=10, plateau=2)
    assert out["exact"] and "*p + g(b)" in out["source"]


def test_plateau_zero_is_greedy_and_misses_it(monkeypatch):
    monkeypatch.setattr(site_edits, "gradient", lambda a: (0, 0, 0) if a.exact else (0, 0, 1))
    from solver import workspace
    monkeypatch.setattr(workspace, "repair_complete", lambda a: a.exact)
    inlined = SRC.replace("    t = g(b);\n    return t + *p;", "    return g(b) + *p;")
    kids = {SRC: [("inline t", "inline_temp", inlined)],
            inlined: [("swap", "commutative", inlined.replace("return g(b) + *p;", "return *p + g(b);"))]}
    monkeypatch.setattr(plateau_search, "_children", lambda code, fn, a, mined=True, llm_repo=None: kids.get(code, []))
    monkeypatch.setattr(site_edits, "site_lines", lambda diff, attr: ({}, "none"))
    score, _ = _scorer(["return *p + g(b);"])
    assert not plateau_search.search(score, SRC, "f", budget=10, plateau=0)["exact"]


def test_order_round_robins_families():
    kids = [(f"d{i}", "decl", f"x{i}") for i in range(5)] + [("c", "commutative", "y"), ("m", "mined:B:k", "z")]
    order = plateau_search._order("code", kids, {})
    assert [k[1] for k in order[:3]].count("decl") == 1

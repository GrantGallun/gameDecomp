"""solver.search_priors and the site_edits.search(reorder=...) hook.

The diagnosis fixture is a real compile of a planted edit (tests/fixtures/site_edits_operators.json).
"""
import json
from pathlib import Path

from solver import search_priors, site_edits, workspace

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "site_edits_operators.json").read_text())


def _edit(kind, i):
    return site_edits.Edit(kind, f"e{i}", 1, 0, 1, str(i))


def test_family_keeps_generator_names_only_where_they_distinguish():
    assert search_priors.family("shape:empty_arm:drop empty then-arm") == "shape:empty_arm"
    assert search_priors.family("pool:temp_inline") == "pool:temp_inline"
    assert search_priors.family("commutative:+@2430") == "commutative"
    assert search_priors.family("mined:some_rule:label") == "mined"
    assert search_priors.family("decl:x: s16 -> s32") == "decl"


def test_features_fire_on_a_real_residual():
    feats = search_priors.features(FIXTURES["decl_width:alLoadNew"]["diff"])
    assert "rule:candidate-only-extension-widens-a-declaration" in feats
    assert [f for f in feats if f.startswith("class:")] and "class:none" not in feats
    assert search_priors.features("") == ["class:none"]


def _table():
    events = ([{"features": ["rule:a"], "family": "decl", "improved": True}] * 5
              + [{"features": ["rule:a"], "family": "decl", "improved": False}]
              + [{"features": ["rule:a"], "family": "literal", "improved": False}] * 10
              + [{"features": ["class:width"], "family": "literal", "improved": True}] * 3)
    return search_priors.fit(events)


def test_order_puts_the_family_that_improved_first(monkeypatch):
    monkeypatch.setattr(search_priors, "features", lambda diff: ["rule:a"])
    edits = [_edit("literal", 0), _edit("literal", 1), _edit("decl", 2)]
    out = search_priors.orderer(_table())("d", edits)
    assert [e.label for e in out] == ["e2", "e0", "e1"]          # stable within a family


def test_drop_removes_only_families_dead_under_every_feature(monkeypatch):
    table = _table()
    edits = [_edit("literal", 0), _edit("decl", 1), _edit("operator", 2)]
    monkeypatch.setattr(search_priors, "features", lambda diff: ["rule:a"])
    assert [e.kind for e in search_priors.orderer(table, drop=True)("d", edits)] == ["decl", "operator"]
    # literal improved under class:width: not dead when the residual also shows that feature
    monkeypatch.setattr(search_priors, "features", lambda diff: ["rule:a", "class:width"])
    assert "literal" in [e.kind for e in search_priors.orderer(table, drop=True)("d", edits)]


def test_unseen_family_is_ranked_at_the_prior_not_dropped(monkeypatch):
    monkeypatch.setattr(search_priors, "features", lambda diff: ["rule:a"])
    out = search_priors.orderer(_table())("d", [_edit("literal", 0), _edit("operator", 1)])
    assert [e.kind for e in out] == ["operator", "literal"]      # prior 0.2 beats literal's 1/15


def test_search_compiles_in_the_order_the_hook_returns(monkeypatch):
    edits = [_edit("literal", i) for i in range(3)]
    monkeypatch.setattr(site_edits, "propose", lambda *a, **k: (list(edits), {}))
    compiled, diffs = [], []

    def score(code, label, parent):
        compiled.append(label)
        return workspace.Attempt(True, 50.0, False, "-lw v0,0x4(a0)\n+lw v0,0x8(a0)", "", "")

    def order(diff, es):
        diffs.append(diff)
        return list(reversed(es))
    site_edits.search(score, "x", "f", budget=3, depth=1, reorder=order)
    assert compiled == ["baseline", "literal:e2", "literal:e1", "literal:e0"]
    assert diffs == ["-lw v0,0x4(a0)\n+lw v0,0x8(a0)"]


PARENT = "-lw v0,0x4(a0)\n+lw v0,0x8(a0)\n-addu v0,v0,v1\n+subu v0,v0,v1"
BETTER = "-lw v0,0x4(a0)\n+lw v0,0x8(a0)"


def _tier_run(monkeypatch, improving: set[str], escalate: bool):
    edits = [_edit("literal", i) for i in range(6)]
    monkeypatch.setattr(site_edits, "propose", lambda *a, **k: (list(edits), {}))
    compiled = []

    def score(code, label, parent):
        compiled.append(label)
        diff = BETTER if label.split(":")[-1] in improving else PARENT
        return workspace.Attempt(True, 50.0, False, diff, "", "")
    site_edits.search(score, "x", "f", budget=6, depth=1, per_step=2, escalate=escalate)
    return compiled[1:]


def test_escalate_pulls_the_next_tier_only_on_a_miss(monkeypatch):
    better = workspace.Attempt(True, 0, False, BETTER, "", "")
    worse = workspace.Attempt(True, 0, False, PARENT, "", "")
    assert site_edits.gradient(better) < site_edits.gradient(worse)
    # tiers (e0,e1) and (e2,e3) improve nothing: the level keeps going and finds e4
    assert _tier_run(monkeypatch, {"e4"}, True) == [f"literal:e{i}" for i in range(6)]
    # without escalation the level stops at its width
    assert _tier_run(monkeypatch, {"e4"}, False) == ["literal:e0", "literal:e1"]
    # a hit in the first tier ends the level there
    assert _tier_run(monkeypatch, {"e0"}, True) == ["literal:e0", "literal:e1"]

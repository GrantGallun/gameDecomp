"""Amendment 20260915-enabling-roots: the const-store enabler fires, stays out of the beam, and the phased search
reaches a child only the enabling root has, while searches without enablers keep their whole budget."""
import inspect

from eval import agentrepair
from solver import regalloc_mutations as rm
from solver import regalloc_search as rs

AERIAL = """void updateRacePlayerMode16AerialTrick(RacePlayer *player) {
    s32 var_v0;
    u32 temp_t3;

    player->stateTimer += 0x1E;
    var_v0 = player->stateTimer;
    if (var_v0 >= 0x401) {
        var_v0 = 0x400;
        player->stateTimer = 0x400;
    }
    temp_t3 = player->stateFlags | 2;
}
"""
NAME = "updateRacePlayerMode16AerialTrick"


def test_const_store_enabler_fires_on_the_aerial_clamp_and_is_not_a_beam_family():
    found = list(rm.enabling_variants(AERIAL, NAME))
    assert [kind for _l, kind, _t in found] == ["const_store_local"]
    assert "        var_v0 = 0x400;\n        player->stateTimer = var_v0;\n" in found[0][2]
    assert not any(kind == "const_store_local" for _l, kind, _t in rm.variants(AERIAL, NAME))
    assert list(rm.enabling_variants("void f(Actor *a) {\n    gCount = 0;\n    a->x = 0;\n}\n", "f")) == []


def _fake(monkeypatch, children):
    class Report:
        def __init__(self, dump):
            self.gradient = {"base": (1, 1, 1), "stored": (1, 1, 1)}.get(dump, (1, 2, 2))
            self.signatures = dump

    monkeypatch.setattr(rs.regalloc_mutations, "variants", lambda source, function, diff="", **_kw: iter(children(source)))
    monkeypatch.setattr(rs.regalloc_signature, "compare", lambda target, dump: Report(dump))


def test_phased_search_reaches_the_enabling_roots_child(monkeypatch):
    _fake(monkeypatch, lambda s: [("field_local:v", "field_local", "EXACT")] if s == "stored"
          else [(f"commutative:{i}", "commutative", f"{s}-{i}") for i in range(40)])
    monkeypatch.setattr(rs.regalloc_mutations, "enabling_variants",
                        lambda source, function: iter([("const_store_local:v@1", "const_store_local", "stored")]))
    compile_candidate = lambda source, label: rs.Compiled(True, source == "EXACT", source)
    assert not rs.search("f", "base", compile_candidate, "t", budget=100).exact
    enabled = rs.search("f", "base", compile_candidate, "t", budget=100, enable=True)
    assert enabled.exact and enabled.log[-1]["parent"] == "const_store_local:v@1"
    root = next(i for i, row in enumerate(enabled.log) if row["depth"] == 0)
    assert root <= 50                                        # phase 1 kept at most half the budget


def test_without_enablers_the_search_is_unchanged(monkeypatch):
    _fake(monkeypatch, lambda s: [(f"commutative:{s}:{i}", "commutative", f"{s}-{i}") for i in range(40)])
    monkeypatch.setattr(rs.regalloc_mutations, "enabling_variants", lambda source, function: iter(()))
    compile_candidate = lambda source, label: rs.Compiled(True, False, source)
    plain = rs.search("f", "base", compile_candidate, "t", budget=100)
    enabled = rs.search("f", "base", compile_candidate, "t", budget=100, enable=True)
    assert plain.compiles == enabled.compiles and plain.compiles > 1 and plain.log == enabled.log


def test_worse_enabling_root_is_not_searched(monkeypatch):
    _fake(monkeypatch, lambda s: [("x", "commutative", "EXACT")] if s == "worse" else [])
    monkeypatch.setattr(rs.regalloc_mutations, "enabling_variants",
                        lambda source, function: iter([("const_store_local:v@1", "const_store_local", "worse")]))
    outcome = rs.search("f", "base", lambda source, label: rs.Compiled(True, source == "EXACT", source), "t",
                        budget=100, enable=True)
    # baseline plus the root, compiled once: its gradient (1,2,2) is worse than the baseline's, so no search from it
    assert not outcome.exact and outcome.compiles == 2


def test_campaign_register_search_enables_roots():
    assert "enable=True" in inspect.getsource(agentrepair._regalloc_search)

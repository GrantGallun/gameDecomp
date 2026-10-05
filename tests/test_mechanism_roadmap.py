"""Measured gates skip a family only in their context; the roadmap learns a gate only when held-out data agrees."""
import json

from eval import mechanism_roadmap
from solver import family_gates, regalloc_mutations

LIBULTRA = {"settings": {"CFLAGS": "-DCOMPILING_LIBULTRA", "C_OPT": "-O1"}}
GAME = {"settings": {"CFLAGS": "-DCOMPILING_LIBULTRA", "C_OPT": "-O2"}}     # game code defines it too


def test_context_reads_state_recipe_and_axis():
    assert family_gates.context("x", None) is None
    ctx = family_gates.context("", {"compiled": True, "frontend": {"passed": False}, "compiler_recipe": LIBULTRA})
    assert ctx == {"state": "bytes_exact", "recipe": "O1", "axis": "none"}
    ctx = family_gates.context("d", {"compiled": True, "frontend": {"passed": False}, "compiler_recipe": GAME})
    assert ctx["state"] == "frontend_rejected" and ctx["recipe"] == "O2"
    assert family_gates.context("d", {"compiled": True})["recipe"] == "unknown"


def test_a_gate_removes_its_family_only_in_its_context(tmp_path, monkeypatch):
    gates = tmp_path / "gates.json"
    gates.write_text(json.dumps({"gates": [{"family": "commutative", "feature": "recipe", "value": "O2"}]}))
    monkeypatch.setattr(family_gates, "GATES", gates)
    source = "void f(s32 a, s32 b) {\n    g(a + b);\n}\n"
    kinds = lambda evidence: {k for _l, k, _c in regalloc_mutations.variants(source, "f", "", evidence=evidence)}
    assert "commutative" in kinds(None)                                      # no evidence: nothing gated
    assert "commutative" not in kinds({"compiled": True, "frontend": {"passed": True}, "compiler_recipe": GAME})
    assert "commutative" in kinds({"compiled": True, "frontend": {"passed": True}, "compiler_recipe": LIBULTRA})


def _world(function, family, improving):
    parent = {"compiled": True, "exact": False, "score": 50.0, "diff": "d", "frontend": {"passed": True}}
    nodes = [{"id": "root", "parent": None, "family": "baseline", "source_sha256": "r", "label": "b", "verdict": parent}]
    for i in range(20):
        good = i < improving
        nodes.append({"id": f"root/{i}", "parent": "root", "family": family, "source_sha256": f"{function}{i}",
                      "label": family, "source": "", "verdict": {"compiled": True, "exact": False,
                                                                  "score": 60.0 if good else 50.0, "diff": "d"}})
    return function, {"nodes": nodes}


def test_gates_are_learned_only_when_both_halves_agree():
    names = [f"f{i}" for i in range(16)]
    halves = {n: mechanism_roadmap.fold(n) for n in names}
    idle = [_world(n, "idle", 0) for n in names]
    assert {g["family"] for g in mechanism_roadmap.gates(idle)["gates"]} == {"idle"}
    # The same family helping on the held-out half is not gated.
    mixed = [_world(n, "idle", 0 if halves[n] == 0 else 5) for n in names]
    assert not mechanism_roadmap.gates(mixed)["gates"]


def test_a_rom_certified_function_is_reached_not_demand():
    # returnToCourseSelectModeMenu (restored-holes-20260925): 31 symbol-alias steps, certified function_exact.
    diff = "--- t\n+++ c\n@@ -1,1 +1,1 @@\n-lw    t9,0x18(a0)\n+lw    t9,0x1c(a0)\n"
    verdict = {"compiled": True, "exact": False, "score": 97.7, "diff": diff}
    world = {"nodes": [{"id": "root", "parent": None, "family": "baseline", "verdict": verdict}]}
    assert any(r["class"] == "field:offset" for r in mechanism_roadmap.demand([("f", world)]))
    verdict["verification"] = {"function_boundary": {"function_exact": True,
                                                     "status": "function_exact_pending_integration"}}
    assert mechanism_roadmap.demand([("f", world)]) == []
    assert not mechanism_roadmap.reached({"compiled": False, "verification": verdict["verification"]})

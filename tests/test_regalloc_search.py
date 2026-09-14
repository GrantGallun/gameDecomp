"""The shared register search: stops at exact, follows the gradient, tolerates plateaus, respects budget."""
from solver import regalloc_search as rs

TARGET = "lhu t6,0x1c(a0)\naddiu t7,t6,1\nsh t7,0x1c(a0)\n"
SOURCE = """void f(Actor *arg0) {
    u16 temp_t7;

    temp_t7 = arg0->unk1C + 1;
    arg0->unk1C = temp_t7;
    if ((temp_t7 & 0xFFFF) >= 0x10) {
        g(arg0);
    }
}
"""


def fake_compiler(exact_when):
    calls = []

    def compile_candidate(source, label):
        calls.append(label)
        if exact_when(source):
            return rs.Compiled(True, True, TARGET)
        # Everything else compiles to the baseline shape with the load in v0.
        return rs.Compiled(True, False, TARGET.replace("t6", "v0"))
    return compile_candidate, calls


def test_search_stops_at_the_first_exact_variant():
    compile_candidate, calls = fake_compiler(lambda s: "++arg0->unk1C >= 0x10" in s)
    outcome = rs.search("f", SOURCE, compile_candidate, TARGET, budget=200)
    assert outcome.exact and "++arg0->unk1C >= 0x10" in outcome.best_source
    assert outcome.best_label.startswith("field_local:")
    assert calls[0] == "baseline" and outcome.compiles == len(calls)


def test_search_respects_budget_and_reports_no_improvement():
    compile_candidate, calls = fake_compiler(lambda s: False)
    outcome = rs.search("f", SOURCE, compile_candidate, TARGET, budget=5)
    assert not outcome.exact and outcome.compiles <= 5
    assert outcome.best_gradient == outcome.baseline_gradient and not outcome.improved


def test_register_dominant_gate():
    assert rs.register_dominant({"register_allocation": 5, "structural": 1})
    assert not rs.register_dominant({"register_allocation": 1, "structural": 4})
    assert not rs.register_dominant({"register_allocation": 5, "structural": 3}, max_other=2)
    assert not rs.register_dominant({"structural": 3})

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


GUIDED = __import__("pathlib").Path(__file__).resolve().parents[1] / "eval/results/uopt-trace-20260914/guided"


def _roster():
    name = "updateCharacterSelectRosterIcons"
    return name, (GUIDED / f"{name}.before.c").read_text(), (GUIDED / f"{name}.exact.c").read_text()


def test_trace_guidance_reaches_the_diagnosed_family_first():
    name, before, exact = _roster()
    unguided_compile, unguided_calls = fake_compiler(lambda s: s == exact)
    unguided = rs.search(name, before, unguided_compile, TARGET, budget=300)
    guided_compile, guided_calls = fake_compiler(lambda s: s == exact)
    reports = []

    def trace(source, label, compiled):
        reports.append(label)
        return {"first": {"class": "selection"}}

    guided = rs.search(name, before, guided_compile, TARGET, budget=300, trace=trace)
    assert unguided.exact and guided.exact and guided.best_source == exact
    assert guided.compiles == 2 < unguided.compiles           # baseline, then the truth test
    assert reports == ["baseline"] and guided.trace_calls == 1 and guided.first_decisions == ["selection"]


def test_diverse_beam_keeps_the_family_the_greedy_beam_pruned(monkeypatch):
    # updateEndingCreditsSlashRisingStar, 2026-09-15: at depth 1 typed_reread (0,9,11) and two tied local_type
    # variants (0,10,13) filled the beam; stmt_move:3->5, tied at (0,10,13), was the parent of the exact child.
    children = {
        "base": [("typed_reread:t1", "typed_reread", "reread"), ("local_type:s32", "local_type", "s32"),
                 ("local_type:u32", "local_type", "u32"), ("stmt_move:3->5", "stmt_move", "moved")],
        "moved": [("field_local:t1", "field_local", "EXACT")],
    }
    gradients = {"base": (0, 16, 21), "reread": (0, 9, 11), "s32": (0, 10, 13), "u32": (0, 10, 13),
                 "moved": (0, 10, 13)}

    class Report:
        def __init__(self, dump):
            self.gradient = gradients.get(dump, (0, 12, 15))
            self.signatures = dump

    def variants(source, function, diff="", prefer=()):
        return iter(children.get(source, [(f"commutative:{source}", "commutative", source + "'")]))

    monkeypatch.setattr(rs.regalloc_mutations, "variants", variants)
    monkeypatch.setattr(rs.regalloc_signature, "compare", lambda target, dump: Report(dump))

    def compile_candidate(source, label):
        return rs.Compiled(True, source == "EXACT", source)

    greedy = rs.search("f", "base", compile_candidate, "target", budget=100)
    diverse = rs.search("f", "base", compile_candidate, "target", budget=100, diverse=True)
    assert not greedy.exact and not any(row["parent"] == "stmt_move:3->5" for row in greedy.log)
    assert diverse.exact and diverse.best_label == "field_local:t1" and diverse.log[-1]["parent"] == "stmt_move:3->5"


def test_enabling_root_reaches_a_child_the_baseline_cannot(monkeypatch):
    # AerialTrick shape: the const-store edit leaves the gradient unchanged; only its child is exact.
    gradients = {"base": (1, 1, 1), "stored": (1, 1, 1)}

    class Report:
        def __init__(self, dump):
            self.gradient = gradients.get(dump, (1, 2, 2))
            self.signatures = dump

    def variants(source, function, diff="", prefer=()):
        if source == "stored":
            return iter([("field_local:v", "field_local", "EXACT")])
        return iter([(f"commutative:{i}", "commutative", f"{source}-{i}") for i in range(3)])

    monkeypatch.setattr(rs.regalloc_mutations, "variants", variants)
    monkeypatch.setattr(rs.regalloc_mutations, "enabling_variants",
                        lambda source, function: iter([("const_store_local:v@1", "const_store_local", "stored")]))
    monkeypatch.setattr(rs.regalloc_signature, "compare", lambda target, dump: Report(dump))

    def compile_candidate(source, label):
        return rs.Compiled(True, source == "EXACT", source)

    plain = rs.search("f", "base", compile_candidate, "target", budget=60)
    enabled = rs.search("f", "base", compile_candidate, "target", budget=60, enable=True)
    assert not plain.exact
    assert enabled.exact and enabled.log[-1]["parent"] == "const_store_local:v@1"
    root_row = next(i for i, row in enumerate(enabled.log) if row["depth"] == 0)
    assert root_row <= 30 and all(row["parent"] != "const_store_local:v@1" for row in enabled.log[:root_row])

def test_diverse_ranks_first_per_family_then_the_rest():
    families = {"a1": "a", "a2": "a", "b1": "b", "a3": "a", "c1": "c"}
    ranked = [(None, None, None, label) for label in ("a1", "a2", "b1", "a3", "c1")]
    assert [item[3] for item in rs._diverse(ranked, 4, families)] == ["a1", "b1", "c1", "a2"]
    assert rs._diverse(ranked, 0, families) == [] and rs._diverse(ranked, -1, families) == []


def test_trace_failures_never_fail_the_search_and_trace_budget_is_respected():
    name, before, exact = _roster()
    compile_candidate, _calls = fake_compiler(lambda s: False)

    def broken(source, label, compiled):
        raise RuntimeError("trace toolchain missing")

    outcome = rs.search(name, before, compile_candidate, TARGET, budget=40, trace=broken, trace_budget=2)
    assert not outcome.exact and outcome.trace_calls <= 2 and outcome.first_decisions[0] == "declined"


# --- keyed resolution: the optimizer key stands in for a compile when it names a known object ---------

def _keyed_world(monkeypatch, objects, children, gradients):
    """`objects` maps a source to the object it compiles to (default: itself); key = that object."""
    class Report:
        def __init__(self, dump):
            self.gradient = gradients.get(dump, (1, 5, 5))
            self.signatures = dump

    def variants(source, function, diff="", prefer=(), evidence=None):
        return iter(children.get(source, []))

    monkeypatch.setattr(rs.regalloc_mutations, "variants", variants)
    monkeypatch.setattr(rs.regalloc_signature, "compare", lambda target, dump: Report(dump))
    compiled = []

    def compile_candidate(source, label):
        compiled.append(source)
        obj = objects.get(source, source)
        return rs.Compiled(True, obj == "EXACT", obj, evidence={"source_attribution": source})
    return compile_candidate, compiled, (lambda source: objects.get(source, source))


def test_keyed_search_takes_the_same_path_with_fewer_compiles(monkeypatch):
    # base has three respellings that compile to base's own object, then the real fix.
    objects = {"base'1": "base", "base'2": "base", "base'3": "base"}
    children = {"base": [("cast:1", "cast", "base'1"), ("cast:2", "cast", "base'2"),
                         ("cast:3", "cast", "base'3"), ("stmt_move:1->2", "stmt_move", "moved")],
                "moved": [("field_local:t1", "field_local", "EXACT")]}
    gradients = {"base": (1, 4, 4), "moved": (1, 2, 2)}
    compile_candidate, plain_calls, key = _keyed_world(monkeypatch, objects, children, gradients)
    plain = rs.search("f", "base", compile_candidate, "target", budget=50)
    keyed_calls_before = len(plain_calls)
    resolved = []
    keyed = rs.search("f", "base", compile_candidate, "target", budget=50, key=key, key_cost=0.0,
                      resolved=lambda c, label, parent, same: resolved.append((c, same)))
    keyed_compiles = plain_calls[keyed_calls_before:]
    assert plain.exact and keyed.exact and keyed.best_source == plain.best_source
    assert keyed.keyed == 3 and keyed.compiles == plain.compiles - 3
    assert not any(s.startswith("base'") for s in keyed_compiles), keyed_compiles
    assert resolved == [("base'1", "base"), ("base'2", "base"), ("base'3", "base")]


def test_a_resolved_plateau_step_is_compiled_for_real_before_it_is_expanded(monkeypatch):
    # The stepping-stone shape: "enabler" compiles to base's object (a no-op), but only its source
    # lets the generator produce the exact child. Keying must not lose it.
    objects = {"enabler": "base"}
    children = {"base": [("temp:1", "temp", "enabler"), ("commutative:1", "commutative", "worse")],
                "enabler": [("stmt_move:1->2", "stmt_move", "EXACT")]}
    gradients = {"base": (1, 4, 4), "worse": (1, 6, 6)}
    compile_candidate, calls, key = _keyed_world(monkeypatch, objects, children, gradients)
    seen_evidence = []
    real_variants = rs.regalloc_mutations.variants

    def spy(source, function, diff="", prefer=(), evidence=None):
        seen_evidence.append((source, (evidence or {}).get("source_attribution")))
        return real_variants(source, function, diff, prefer, evidence)
    monkeypatch.setattr(rs.regalloc_mutations, "variants", spy)
    outcome = rs.search("f", "base", compile_candidate, "target", budget=50, key=key, key_cost=0.0)
    assert outcome.exact and outcome.best_label == "stmt_move:1->2"
    assert outcome.keyed == 1 and calls.count("enabler") == 1        # resolved first, compiled to expand
    assert ("enabler", "enabler") in seen_evidence                    # its OWN attribution, not base's


def test_key_time_is_charged_to_the_budget(monkeypatch):
    children = {"base": [(f"cast:{i}", "cast", f"base'{i}") for i in range(20)]}
    objects = {f"base'{i}": "base" for i in range(20)}
    compile_candidate, calls, key = _keyed_world(monkeypatch, objects, children, {"base": (1, 4, 4)})
    outcome = rs.search("f", "base", compile_candidate, "target", budget=5, key=key, key_cost=0.5)
    assert outcome.compiles + 0.5 * outcome.key_calls <= 5 + 0.5
    assert outcome.keyed == outcome.key_calls - 1                     # every key after the baseline's hit


def test_a_wrong_key_is_logged_and_the_oracle_still_decides(monkeypatch):
    # The key claims "enabler" is base's object; its real compile is exact. The search must say so.
    children = {"base": [("temp:1", "temp", "enabler")], "enabler": []}
    compile_candidate, calls, _key = _keyed_world(monkeypatch, {"enabler": "EXACT"}, children,
                                                  {"base": (1, 4, 4)})
    outcome = rs.search("f", "base", compile_candidate, "target", budget=50,
                        key=lambda s: "base" if s in ("base", "enabler") else s, key_cost=0.0)
    assert outcome.exact and outcome.best_source == "enabler"
    assert any(row.get("key_violation") for row in outcome.log)


def test_rank_by_sites_orders_owner_lines_first_then_silent_lines_and_never_drops_one():
    from solver.residual_sites import digest
    source = "void f(int *a) {\n    int x;\n    x = a[1];\n    a[2] = x;\n}\n"
    diff = "--- a\n+++ b\n@@ -3 +3 @@\n-lw v1,0x4(a0)\n+lw v0,0x4(a0)\n"
    row = {"section": ".text", "address": 0, "bytes": "00000000"}
    attribution = {"status": "verified", "source_sha256": digest(source), "diff_sha256": digest(diff),
                   "instructions": [{**row, "normalized_line": 3, "candidate_line": 3},   # owns the mismatch
                                    {**row, "normalized_line": 4, "candidate_line": 4}]}  # emits, matches
    parent = rs.Compiled(True, False, "dump", diff, {"source_attribution": attribution})
    live = ("live", "k", source.replace("a[2] = x;", "a[2] = x + 0;"))
    silent = ("silent", "k", source.replace("int x;", "unsigned int x;"))
    owner = ("owner", "k", source.replace("x = a[1];", "x = *(a + 1);"))
    ranked = rs.rank_by_sites(source, "f", parent, [live, silent, owner])
    assert [v[0] for v in ranked] == ["owner", "silent", "live"]
    # no verified attribution: the generator's order stands
    bare = rs.Compiled(True, False, "dump", diff, None)
    assert rs.rank_by_sites(source, "f", bare, [live, silent, owner]) == [live, silent, owner]


# --- checking reuse (review follow-up 2026-09-28 §2) -------------------------------------------------

def _pruning_world(monkeypatch):
    """A wrong key says "a" is base's object. Reused, "a" ranks as sideways and the beam of one takes "b"
    (really worse than "a"); only "a" leads to the exact child. Object identity is the dump."""
    children = {"base": [("temp:a", "temp", "a"), ("stmt_move:b", "stmt_move", "b")],
                "a": [("field_local:x", "field_local", "EXACT")], "b": []}
    gradients = {"base": (1, 4, 4), "a": (1, 2, 2), "b": (1, 3, 3)}
    compile_candidate, calls, _key = _keyed_world(monkeypatch, {}, children, gradients)
    wrong_key = lambda s: "base" if s in ("base", "a") else s
    return compile_candidate, calls, wrong_key


def test_without_a_check_a_wrong_key_prunes_the_path_to_the_match(monkeypatch):
    compile_candidate, _calls, wrong_key = _pruning_world(monkeypatch)
    outcome = rs.search("f", "base", compile_candidate, "target", budget=20, beam=1, depth=3,
                        key=wrong_key, key_cost=0.0)
    assert not outcome.exact and outcome.keyed == 1               # the failure the audit exists for


def test_an_audit_catches_the_wrong_key_and_the_restart_finds_the_match(monkeypatch):
    compile_candidate, _calls, wrong_key = _pruning_world(monkeypatch)
    outcome = rs.search("f", "base", compile_candidate, "target", budget=20, beam=1, depth=3,
                        key=wrong_key, key_cost=0.0, audit_rate=1.0)
    assert outcome.exact and outcome.best_source == "EXACT"
    assert outcome.key_violations == 1 and outcome.audited == 1 and outcome.audit_conclusive == 1
    assert any(row.get("key_restart") for row in outcome.log)
    assert outcome.key_disabled.startswith("restarted without reuse")


def test_the_certificate_decides_not_the_listing(monkeypatch):
    # Same listing, but the object certificate says the objects differ: that is a violation.
    children = {"base": [("cast:1", "cast", "base'")], "base'": []}
    compile_candidate, _calls, _key = _keyed_world(monkeypatch, {"base'": "base"}, children, {"base": (1, 4, 4)})
    verdicts = []

    def certify(prior, actual):
        verdicts.append((prior.evidence or {}).get("source_attribution"))
        return False
    outcome = rs.search("f", "base", compile_candidate, "target", budget=20, key=_key, key_cost=0.0,
                        same_object=certify, audit_rate=1.0)
    assert verdicts and outcome.key_violations == 1 and outcome.key_disabled.startswith("restarted")


def test_an_unavailable_check_is_not_agreement_and_stops_reuse(monkeypatch):
    children = {"base": [(f"cast:{i}", "cast", f"base'{i}") for i in range(6)]}
    objects = {f"base'{i}": "base" for i in range(6)}
    compile_candidate, calls, key = _keyed_world(monkeypatch, objects, children, {"base": (1, 4, 4)})
    outcome = rs.search("f", "base", compile_candidate, "target", budget=40, key=key, key_cost=0.0,
                        same_object=lambda prior, actual: None, audit_rate=1.0)
    assert outcome.audited == 1 and outcome.audit_unavailable == 1 and outcome.key_violations == 0
    assert outcome.key_disabled.startswith("audit check unavailable")
    assert outcome.key_calls == 2 and outcome.keyed == 0           # baseline + the audited one, then none
    assert outcome.reuse_events == outcome.keyed + outcome.audited


def test_audit_sampling_is_seeded_and_counted(monkeypatch):
    children = {"base": [(f"cast:{i}", "cast", f"base'{i}") for i in range(40)]}
    objects = {f"base'{i}": "base" for i in range(40)}
    compile_candidate, _calls, key = _keyed_world(monkeypatch, objects, children, {"base": (1, 4, 4)})
    runs = [rs.search("f", "base", compile_candidate, "target", budget=100, key=key, key_cost=0.0,
                      audit_rate=0.25, audit_seed=7) for _ in range(2)]
    assert runs[0].audited == runs[1].audited > 0                  # reproducible
    for r in runs:
        assert r.reuse_events == r.keyed + r.audited == 40 and r.audit_conclusive == r.audited
        assert r.key_violations == 0

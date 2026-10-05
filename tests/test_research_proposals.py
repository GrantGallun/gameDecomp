"""Small mechanism tests for opt-in research source proposals."""

from eval.research_suite.proposals import compose, infer_views, pure_variants, type_variants


def test_compose_retains_a_two_step_source_path_without_compiler_feedback():
    initial = "unsigned int f(unsigned int x) { return (x + 1U) + 2U; }"

    def generate(source):
        return pure_variants(source, "f")

    candidates = compose(initial, generate, max_depth=2)
    final = next(item for item in candidates if "x + 3U" in item["source"])
    assert len(final["path"]) == 2
    assert final["source"] != initial
    assert all(set(item) >= {"source", "label", "family", "path"} for item in candidates)
    assert all("compiled" not in item for item in candidates)


def test_compose_obeys_candidate_and_depth_budgets():
    def generate(source):
        n = int(source)
        return [("plus-one", "synthetic", str(n + 1)), ("plus-two", "synthetic", str(n + 2))]

    assert [c["source"] for c in compose("0", generate, max_depth=1)] == ["1", "2"]
    assert len(compose("0", generate, max_depth=8, max_candidates=2)) == 2
    assert compose("0", generate, max_depth=0) == []
    assert compose("0", generate, max_candidates=0) == []


def test_pure_variants_declines_side_effects_and_signed_arithmetic():
    assert list(pure_variants("int f(int x) { return (x + 1) + 2; }", "f")) == []
    assert list(pure_variants("unsigned int f(unsigned int x) { return (x++ + 1U) + 2U; }", "f")) == []
    assert list(pure_variants("unsigned int f(unsigned int x) { return (x + 0xffffffffU) + 2U; }", "f")) == []


def test_evidence_backed_view_emits_changed_compilable_c():
    source = "unsigned int f(unsigned char *p) { return *(unsigned int *)(p + 4); }"
    report = infer_views([{
        "base": "f:p", "offset": 4, "width": 4, "signed": False,
        "kind": "load", "evidence_id": "lw@0x10",
    }])
    candidates = type_variants(source, "f", report)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["source"] != source
    assert "unsigned char unknown_0[4]" in candidate["source"]
    assert "unsigned int field_4" in candidate["source"]
    assert "p->field_4" in candidate["source"]
    assert candidate["evidence_ids"] == ["lw@0x10"]
    assert candidate["family"] == "type_view"


def test_conflicts_are_explicit_alternatives_and_are_not_emitted_as_facts():
    accesses = [
        {"base": "f:p", "offset": 4, "width": 4, "signed": False, "kind": "load", "evidence_id": "lw@4"},
        {"base": "f:p", "offset": 4, "width": 2, "signed": False, "kind": "load", "evidence_id": "lhu@4"},
    ]
    report = infer_views(accesses)
    view = report["views"][0]
    assert view["status"] == "conflict"
    assert {a["fields"][0]["width"] for a in view["alternatives"]} == {2, 4}
    source = "unsigned int f(unsigned char *p) { return *(unsigned int *)(p + 4); }"
    assert type_variants(source, "f", report) == []


def test_overlapping_access_intervals_are_conflicts_even_at_different_offsets():
    report = infer_views([
        {"base": "f:p", "offset": 0, "width": 4, "signed": False, "kind": "load", "evidence_id": "lw@0"},
        {"base": "f:p", "offset": 2, "width": 2, "signed": False, "kind": "load", "evidence_id": "lhu@2"},
    ])
    view = report["views"][0]
    assert view["status"] == "conflict"
    assert view["fields"] == []
    assert {a["offset"] for a in view["alternatives"]} == {0, 2}


def test_missing_evidence_and_unscoped_names_are_rejected():
    report = infer_views([
        {"base": "f:p", "offset": 4, "width": 4, "signed": False, "kind": "load"},
        {"base": "p", "offset": 4, "width": 4, "signed": False, "kind": "load", "evidence_id": "lw@4"},
    ], [{"from": "f:p", "to": "f:q"}])
    assert report["views"] == []
    assert len(report["rejected"]) == 3


def test_flow_propagates_cited_pointer_view_within_function_only():
    report = infer_views(
        [{"base": "f:q", "offset": 4, "width": 4, "signed": False, "kind": "load", "evidence_id": "lw@4"}],
        [{"from": "f:p", "to": "f:q", "evidence_id": "move@2"},
         {"from": "g:p", "to": "f:q", "evidence_id": "bad-cross-scope"}],
    )
    view = next(v for v in report["views"] if v["base"] == "f:p")
    assert view["fields"][0]["evidence_ids"] == ["lw@4", "move@2"]
    assert len(report["rejected"]) == 1


def test_uncited_layout_is_unknown_even_when_flow_itself_is_cited():
    report = infer_views([], [{"from": "f:p", "to": "f:q", "evidence_id": "move@2"}])
    assert {view["status"] for view in report["views"]} == {"unknown"}
    assert all(view["fields"] == [] for view in report["views"])


def test_type_variant_requires_original_parameter_type_and_matching_access():
    report = infer_views([{
        "base": "f:p", "offset": 4, "width": 4, "signed": False,
        "kind": "load", "evidence_id": "lw@4",
    }])
    assert type_variants("unsigned int f(void *p) { return *(unsigned int *)(p + 4); }", "f", report) == []
    assert type_variants("unsigned int f(unsigned char *p) { return *(unsigned int *)(p + 8); }", "f", report) == []
    assert type_variants("unsigned int f(unsigned char *p) { return *(unsigned int *)(p++ + 4); }", "f", report) == []


def test_pure_rewrites_decline_octal_literals_in_decimal_only_grammar():
    from eval.research_suite.proposals import pure_variants
    assert pure_variants('unsigned int f(unsigned int x) { return (x + 010U) + 2U; }', 'f') == []
    assert pure_variants('unsigned int f(unsigned int x) { return x + (1U + 010U); }', 'f') == []

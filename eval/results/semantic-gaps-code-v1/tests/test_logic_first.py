import json

from eval import logic_first


def _row(name, tu, score=90.0, insns=20):
    return {
        "function": name,
        "tu_id": tu,
        "instruction_count": insns,
        "attempt_id": 1,
        "source_sha256": "x",
        "stored_weighted_score": score,
    }


def test_connected_cluster_uses_only_binary_connected_candidates():
    rows = [
        _row("a", 1, insns=10), _row("b", 1), _row("c", 2),
        _row("isolated", 1, score=100.0),
    ]
    cluster, edges = logic_first.connected_cluster(
        rows, {("a", "b"), ("b", "c")}, limit=3)

    assert {row["function"] for row in cluster} == {"a", "b", "c"}
    assert {tuple(edge.values()) for edge in edges} == {
        ("a", "b"), ("b", "c")}


def test_connected_cluster_reports_component_shortfall_naturally():
    rows = [_row("a", 1), _row("b", 1), _row("c", 1)]

    cluster, edges = logic_first.connected_cluster(
        rows, {("a", "b")}, limit=3)

    assert len(cluster) == 2
    assert edges == [{"caller": "a", "callee": "b"}]


def test_connected_cluster_falls_back_to_ranked_independent_leaves():
    rows = [
        _row("low", 1, score=70.0, insns=4),
        _row("large", 2, score=90.0, insns=30),
        _row("small", 3, score=90.0, insns=10),
    ]

    cluster, edges = logic_first.connected_cluster(rows, set(), limit=2)

    assert [row["function"] for row in cluster] == ["small", "large"]
    assert edges == []


def test_manifest_digest_ignores_only_its_digest_field():
    value = {"kind": "logic", "cluster": [{"function": "f"}]}
    value["manifest_digest"] = logic_first._digest(value)

    assert value["manifest_digest"] == logic_first._digest(value)
    value["cluster"][0]["function"] = "g"
    assert value["manifest_digest"] != logic_first._digest(value)


def test_settled_functions_requires_semantics_and_both_side_coverage(tmp_path):
    receipt = tmp_path / "wave.json"
    receipt.write_text(json.dumps({"nodes": [
        {"function": "exact", "exact": True},
        {"function": "settled", "semantic_settled": True},
        {"function": "legacy-good",
         "all_observed_semantic_cases_passed": True,
         "target_coverage": {"status": "complete"},
         "candidate_coverage": {"status": "complete"}},
        {"function": "partial",
         "all_observed_semantic_cases_passed": True,
         "target_coverage": {"status": "partial"},
         "candidate_coverage": {"status": "complete"}},
    ]}))

    assert logic_first.settled_functions((receipt,)) == {
        "exact", "settled", "legacy-good"}


def test_settled_functions_accepts_strict_standalone_stress_receipt(tmp_path):
    receipt = tmp_path / "stress.json"
    receipt.write_text(json.dumps({
        "config": {"function": "leaf"},
        "candidates": [{
            "attempt": {"compiled": True},
            "differential": {
                "all": {"passed": 12, "failed": 0, "inconclusive": 0},
                "target_coverage": {"status": "complete"},
                "candidate_coverage": {"status": "complete"},
            },
        }],
    }))

    assert logic_first.settled_functions((receipt,)) == {"leaf"}


def test_settled_functions_accepts_full_dag_observational_pass(tmp_path):
    receipt = tmp_path / "dag.json"
    receipt.write_text(json.dumps({
        "dag": {"nodes": [{
            "function": "caller",
            "attempt": {"compiled": True, "exact": False},
            "combined_target_coverage": {"status": "complete"},
            "combined_candidate_coverage": {"status": "complete"},
            "differential": {
                "statuses": {"passed": 10},
            },
            "semantic_authoritative": False,
        }]},
    }))

    assert logic_first.settled_functions((receipt,)) == {"caller"}


def test_module_packet_keeps_binary_context_and_semantic_boundary():
    assessment = {
        "stage": "logic_shape_candidate", "semantic_status": "not_tested",
        "metrics": {"memory_effects": 1.0},
        "gates": {"calls_agree": True, "structural_control": False},
        "target": {"direct_call_sequence": ["helper"]},
    }
    receipt = {
        "binary_call_edges": [{"caller": "f", "callee": "helper"}],
        "functions": [
            {"function": "f", "assessment": assessment},
            {"function": "helper", "assessment": assessment},
        ],
    }

    packet = logic_first.module_packet(receipt, "f")

    assert packet["function"] == "f"
    assert len(packet["functions"]) == 2
    assert packet["policies"][
        "semantic_equivalence_requires_separate_behavioral_evidence"]
    assert "structural_control" in packet["functions"][0]["failed_gates"]
    assert "packet_digest" in packet

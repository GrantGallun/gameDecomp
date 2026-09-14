"""Tests for transparent Ghidra BSim feature-overlap triage."""

import pytest

from solver import ghidra_similarity


def evidence(features, settings=77, completed=True):
    return {
        "function": {"entry": "80001000"},
        "bsim_signature": {
            "completed": completed,
            "error": "failed" if not completed else "",
            "settings": settings,
            "feature_count": len(features),
            "features": features,
        },
    }


def semantic_evidence(opcodes, mnemonics=None, *, constants=None, cfg=None):
    constants = constants or [1] * len(opcodes)
    operations = []
    for index, (opcode, constant) in enumerate(zip(opcodes, constants)):
        operations.append({
            "opcode": opcode,
            "output": f"(unique, 0x{100 + index:x}, 4)",
            "inputs": [f"(const, 0x{constant:x}, 4)",
                       f"(register, 0x{index:x}, 4)"],
        })
    cfg = cfg or [([], [1]), ([0], [])]
    return {
        "function": {"entry": "80001000"},
        "normalized_ir": {
            "completed": True,
            "error": "",
            "operations": operations,
            "basic_blocks": [
                {"in": incoming, "out": outgoing}
                for incoming, outgoing in cfg
            ],
        },
        "instructions": [
            {"mnemonic": mnemonic}
            for mnemonic in (mnemonics or ["addiu"] * len(opcodes))
        ],
    }


def test_identical_feature_multisets_score_one():
    result = ghidra_similarity.compare_bsim(
        evidence(["a", "b", "b"]), evidence(["b", "a", "b"]))
    assert result["identical_multiset"] is True
    assert result["multiset_jaccard"] == 1.0
    assert result["dice"] == 1.0
    assert result["cosine"] == pytest.approx(1.0)


def test_overlap_preserves_multiplicity_and_containment():
    result = ghidra_similarity.compare_bsim(
        evidence(["a", "b", "b"]), evidence(["b", "b", "b", "c"]))
    assert result["shared_feature_occurrences"] == 2
    assert result["shared_unique_features"] == 1
    assert result["multiset_jaccard"] == pytest.approx(2 / 5)
    assert result["left_containment"] == pytest.approx(2 / 3)
    assert result["right_containment"] == pytest.approx(1 / 2)


def test_incompatible_or_failed_signatures_are_rejected():
    with pytest.raises(ValueError, match="settings differ"):
        ghidra_similarity.compare_bsim(
            evidence(["a"]), evidence(["a"], settings=73))
    with pytest.raises(ValueError, match="no completed"):
        ghidra_similarity.compare_bsim(
            evidence(["a"]), evidence([], completed=False))


def test_semantic_comparison_masks_addresses_but_reports_literal_drift():
    left = semantic_evidence(
        ["COPY", "INT_ADD", "RETURN"], constants=[1, 2, 3])
    right = semantic_evidence(
        ["COPY", "INT_ADD", "RETURN"], constants=[1, 99, 3])
    # Temp/register offsets are already different objects; they are deliberately
    # erased so cross-program address assignment cannot dominate retrieval.
    right["normalized_ir"]["operations"][0]["output"] = \
        "(unique, 0xdead, 4)"

    result = ghidra_similarity.compare_semantics(left, right)
    assert result["operation_shape"]["identical_sequence"] is True
    assert result["operation_literals"]["identical_sequence"] is False
    assert result["provenance_rank_score"] == pytest.approx(1.0)
    assert result["role"].startswith("provenance ranking only")


def test_semantic_comparison_penalizes_different_program_shape():
    left = semantic_evidence(
        ["COPY", "INT_ADD", "INT_MULT", "RETURN"],
        ["lw", "addu", "mult", "jr"])
    right = semantic_evidence(
        ["LOAD", "CBRANCH", "CALL", "RETURN"],
        ["lb", "beq", "jal", "jr"],
        cfg=[([], [1, 2]), ([0], [2]), ([0, 1], [])])
    result = ghidra_similarity.compare_semantics(left, right)
    assert result["provenance_rank_score"] < 0.6
    assert result["operation_shape"]["identical_sequence"] is False


def test_combined_comparison_labels_the_non_oracle_role():
    left = semantic_evidence(["COPY", "RETURN"])
    right = semantic_evidence(["COPY", "RETURN"])
    left["bsim_signature"] = evidence(["x"])["bsim_signature"]
    right["bsim_signature"] = evidence(["x"])["bsim_signature"]
    result = ghidra_similarity.compare(left, right)
    assert result["schema_version"] == 2
    assert result["bsim"]["identical_multiset"] is True
    assert result["semantic"]["provenance_rank_score"] == pytest.approx(1.0)
    assert "never an exactness oracle" in result["role"]

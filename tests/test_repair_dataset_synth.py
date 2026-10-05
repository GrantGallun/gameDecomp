"""The synthetic repair dataset's contract, pinned without needing a compiler.

The properties that matter here are the ones whose failure is invisible:

- a mutation that produces an object IDENTICAL to the target is not a repair task, and would be
  counted as a success (this is measured on the real compiler in `probe_mutations`, and asserted
  structurally here);
- a task whose solver-visible input contains the generator's answer would make the whole
  evaluation fiction;
- a split that separates two seeds of one template family measures memorisation, not capability;
- a frozen manifest that can be rewritten is not frozen.

Every leakage test asserts the check FIRES on a real leak, not merely that it stays silent on
clean data. An always-silent check and a correct check are indistinguishable from outside.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import repair_dataset_synth as rds
from eval import repair_mutations as rm


# --- mutations ----------------------------------------------------------------

def test_every_mutation_declares_an_axis_and_an_expectation():
    assert rm.MUTATIONS, "the catalog must not be empty"
    for name, mutation in rm.MUTATIONS.items():
        assert mutation.fault.axis in ("structural", "regalloc", "layout", "reloc",
                                       "ordering", "immediate")
        assert mutation.families, f"{name} applies to no family"
        assert len(mutation.expectation) > 20, f"{name} does not say what it expects"
        assert callable(mutation.apply)


def test_a_mutation_declines_rather_than_crashing_on_material_it_does_not_match():
    """Declining is the easy half, so it is tested -- but it is not the half that broke."""
    for name, mutation in rm.MUTATIONS.items():
        try:
            result = mutation.apply("int unrelated(void) { return 0; }")
        except Exception as exc:                       # noqa: BLE001 - any raise is a bug
            pytest.fail(f"{name} raised on foreign input: {type(exc).__name__}: {exc}")
        assert result is None or isinstance(result, str)


def test_candidates_for_only_returns_mutants_that_keep_the_function_identity():
    source = 's32 syn_loop_for_9(s32 arg0, s32 arg1) {\n    s32 i;\n\n    i = 0;\n    return i;\n}\n'
    for name, damaged in rm.candidates_for("loop_for", source):
        assert rm.keeps_identity("syn_loop_for_9", damaged), name
        assert damaged != source


def test_keeps_identity_rejects_a_renamed_or_removed_definition():
    good = "s32 f(s32 a) {\n    return a;\n}\n"
    assert rm.keeps_identity("f", good)
    assert not rm.keeps_identity("f", good.replace("f(", "g("))
    assert not rm.keeps_identity("f", "s32 g(void) { return 0; }\n")
    assert not rm.keeps_identity("f", "")


def test_a_forward_declaration_is_not_mistaken_for_a_definition():
    """`extern s32 syn_ext(s32);` is a call target, not something the unit defines.

    Counting it made the identity check report every regalloc mutant as renamed, which would
    have silently excluded the two families that exercise register allocation.
    """
    source = ("extern s32 syn_ext(s32);\n\n"
              "s32 syn_saved_regs_0(s32 arg0) {\n    return syn_ext(arg0);\n}\n")
    assert rm.function_names(source) == ["syn_saved_regs_0"]
    assert rm.keeps_identity("syn_saved_regs_0", source)


# --- leakage ------------------------------------------------------------------

def _task(**overrides) -> rds.Task:
    clean = ("#include \"common.h\"\n\n"
             "s32 syn_x(s32 arg0, s32 arg1) {\n"
             "    s32 acc;\n\n"
             "    acc = arg0 * 3;\n"
             "    acc += arg1;\n"
             "    return acc;\n"
             "}\n")
    candidate = clean.replace("acc += arg1;", "acc -= arg1;")
    base = dict(
        task_id="syn:loop_for:0:narrow-locals", family="loop_for", seed=0,
        mutation="narrow-locals", fault_axis="immediate", function="syn_x",
        group="loop_for", source_kind="synthetic",
        input={"assembly": "lw v0,0(a0)\naddiu v0,v0,3",
               "candidate": candidate,
               "feedback": {"kind": "instruction-diff", "text": "- addiu\n+ subu"}},
        parent={"compiled": True, "score": 70.0, "asm": "addiu v0,v0,3"},
        target={"source_sha256": rds.sha_text(clean), "asm": "addiu v0,v0,3", "exact": True},
        child={"exact": False, "outcome": "unrepaired"},
        generator_source=clean,
    )
    base.update(overrides)
    return rds.Task(**base)


def test_a_clean_task_passes_the_leakage_check():
    report = rds.leakage_check(_task())
    assert report["clean"], report["findings"]
    assert report["answer_ngrams"] > 0, "the n-gram extractor found nothing to check"
    assert report["answer_ngrams_outside_the_candidate"] == 0
    assert report["defect_visible_in_candidate"] is True


def test_the_check_fires_when_the_generator_source_is_in_the_input():
    """The real leak: the answer, verbatim, where the solver can read it."""
    task = _task()
    task.input["candidate"] = task.generator_source
    report = rds.leakage_check(task)
    assert not report["clean"]
    assert any("full generator source" in f or "byte-identical" in f for f in report["findings"])


def test_the_check_fires_when_the_answer_is_merely_echoed_into_the_feedback():
    task = _task()
    task.input["feedback"]["text"] = "expected:\n" + task.generator_source
    report = rds.leakage_check(task)
    assert not report["clean"], "echoing the answer through the feedback channel is still a leak"


def test_the_check_fires_when_only_the_source_digest_leaks():
    task = _task()
    task.input["assembly"] = "sha256=" + rds.sha_text(task.generator_source)
    report = rds.leakage_check(task)
    assert not report["clean"]
    assert any("hash" in f for f in report["findings"])


def test_shared_declarations_and_a_small_defect_are_not_reported_as_leaks():
    """A one-line mutation leaves most of the answer's statements in the candidate.

    That is not a leak -- the solver was HANDED the candidate, so those lines carry no
    information it does not have. Counting them flagged 32 of 65 valid tasks before this was
    fixed, which is a check that fires on everything and therefore measures nothing.
    """
    task = _task()
    report = rds.leakage_check(task)
    # The fixture is a small defect: the candidate shares nearly all of the answer's n-grams.
    handed = " ".join(task.input["candidate"].split())
    shared = [g for g in rds.source_ngrams(task.generator_source) if g in handed]
    assert len(shared) >= max(1, report["answer_ngrams"] // 2), (
        "this fixture is supposed to be a SMALL defect")
    assert report["clean"], report["findings"]
    assert report["answer_ngrams_outside_the_candidate"] == 0


def test_assembly_is_not_checked_for_answer_text_because_it_cannot_contain_it():
    """The assembly IS the target and is meant to be shown. Only source-shaped leaks matter."""
    task = _task()
    task.input["assembly"] = "s32 syn_x(s32 arg0) { /* this is the target listing */ }"
    report = rds.leakage_check(task)
    assert report["clean"], report["findings"]


# --- splits and the frozen manifest -------------------------------------------

def _rows():
    out = []
    for family in ("loop_for", "stack_spill", "switch_sparse", "if_chain", "saved_regs"):
        for seed in (0, 1):
            task = _task(task_id=f"syn:{family}:{seed}:narrow-locals", family=family,
                         seed=seed, group=family)
            out.append(task.as_dict())
    return out


def test_a_whole_family_goes_to_one_side_and_never_both():
    rows = rds.assign_splits(_rows(), test_families=("loop_for", "stack_spill"))
    by_split: dict[str, set[str]] = {}
    for row in rows:
        by_split.setdefault(row["split"], set()).add(row["family"])
    assert by_split["test"] == {"loop_for", "stack_spill"}
    assert by_split["train"] == {"switch_sparse", "if_chain", "saved_regs"}
    assert not (by_split["test"] & by_split["train"])
    # Every seed of a test family is on the test side.
    assert {r["seed"] for r in rows if r["family"] == "loop_for"} == {0, 1}


def test_the_manifest_refuses_to_be_rewritten(tmp_path):
    """A split that can be rewritten after seeing a result is not a frozen split."""
    rows = rds.assign_splits(_rows(), test_families=("loop_for",))
    path = tmp_path / "FROZEN_SPLIT.json"
    rds.freeze_manifest(rows, manifest_path=path, recipe={"compiler": "ido-5.3"},
                        test_families=("loop_for",))
    first = json.loads(path.read_text())
    with pytest.raises(SystemExit) as excinfo:
        rds.freeze_manifest(rows, manifest_path=path, recipe={"compiler": "ido-5.3"},
                            test_families=("loop_for",))
    assert "already exists" in str(excinfo.value)
    assert json.loads(path.read_text())["manifest_sha256"] == first["manifest_sha256"]


def test_the_manifest_records_a_real_assembly_overlap_and_not_a_placeholder(tmp_path):
    """The first version hashed an absent key, so both sides hashed to the empty digest and the
    check reported a collision that did not exist."""
    rows = rds.assign_splits(_rows(), test_families=("loop_for",))
    for row in rows:
        row["parent"]["asm"] = f"asm-for-{row['task_id']}"
        row["parent"].pop("asm_sha256", None)
    path = tmp_path / "FROZEN_SPLIT.json"
    manifest = rds.freeze_manifest(rows, manifest_path=path, recipe={},
                                   test_families=("loop_for",))
    empty = rds.sha_text("")
    assert empty not in manifest["assembly_overlap_train_test"]
    assert manifest["assembly_overlap_train_test"] == []
    assert all(r["parent"]["asm_sha256"] for r in rows)


def test_a_genuine_assembly_collision_between_splits_is_reported(tmp_path):
    rows = rds.assign_splits(_rows(), test_families=("loop_for",))
    for row in rows:
        row["parent"]["asm"] = "the same object twice"
        row["parent"].pop("asm_sha256", None)
    manifest = rds.freeze_manifest(rows, manifest_path=tmp_path / "F.json", recipe={},
                                   test_families=("loop_for",))
    assert manifest["assembly_overlap_train_test"] == [rds.sha_text("the same object twice")]


# --- the record shape ---------------------------------------------------------

def test_the_solver_visible_input_has_exactly_three_fields():
    task = _task()
    assert set(task.input) == set(rds.INPUT_FIELDS)
    assert "generator_source" not in task.input
    visible = task.visible_text()
    assert task.generator_source.strip() not in visible


def test_a_candidate_identical_to_the_target_is_not_a_task():
    """A no-op mutation would be counted as a success. It is measured, and refused."""
    clean = "s32 syn_x(s32 a) {\n    return a;\n}\n"
    payload = b"identical-object-bytes"
    result = rds.build_task(
        family="loop_for", seed=0, mutation="narrow-locals", candidate_source=clean,
        clean_source=clean, function="syn_x",
        compiled={"target": {"object": payload, "asm": "addiu", "instructions": 1,
                             "compiled": True, "stderr": ""},
                  "parent": {"object": payload, "asm": "addiu", "instructions": 1,
                             "compiled": True, "stderr": ""}})
    assert result is None


def test_the_object_diff_counts_length_and_position_separately():
    same = rds.object_diff(b"abcd", b"abcd")
    assert same["identical"] and same["differing_bytes"] == 0 and same["first_difference"] is None

    one_byte = rds.object_diff(b"abcd", b"abXd")
    assert not one_byte["identical"]
    assert one_byte["first_difference"] == 2 and one_byte["differing_bytes"] == 1

    shorter = rds.object_diff(b"abcdef", b"abc")
    assert shorter["first_difference"] == 3
    assert shorter["differing_bytes"] == 3, "the missing tail counts as differing"
    assert shorter["target_bytes"] == 6 and shorter["candidate_bytes"] == 3


def test_a_similarity_score_is_zero_for_a_failed_compile_and_100_only_when_identical():
    assert rds.score_of(None, False) == 0.0
    assert rds.score_of(rds.object_diff(b"abc", b"abc"), True) == 100.0
    partial = rds.score_of(rds.object_diff(b"abcdefgh", b"abcdWXYZ"), True)
    assert 0.0 < partial < 100.0


def test_feedback_prefers_the_compiler_error_when_the_candidate_did_not_build():
    broken = rds.feedback_for(parent_compiled=False, stderr="cfe: Error: line 3: syntax error",
                              target_asm="addiu v0,v0,3", parent_asm="")
    assert broken["kind"] == "compile-error"
    assert "syntax error" in broken["text"]

    diff = rds.feedback_for(parent_compiled=True, stderr="",
                            target_asm="addiu v0,v0,3", parent_asm="addiu v0,v0,4")
    assert diff["kind"] == "instruction-diff"
    assert "-addiu v0,v0,3" in diff["text"] or "- addiu v0,v0,3" in diff["text"]
    assert "+addiu v0,v0,4" in diff["text"] or "+ addiu v0,v0,4" in diff["text"]


def test_the_feedback_never_contains_the_generator_source():
    """The feedback is derived from the target and the candidate only."""
    clean = "s32 syn_x(s32 a) {\n    return a * 7;\n}\n"
    damaged = clean.replace("* 7", "* 9")
    feedback = rds.feedback_for(parent_compiled=True, stderr="",
                                target_asm="li v0,7\nmul", parent_asm="li v0,9\nmul")
    assert clean.strip() not in feedback["text"]
    assert damaged.strip() not in feedback["text"]

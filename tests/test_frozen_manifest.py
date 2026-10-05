"""What a frozen evaluation has to bind, and the guards that fire when it does not.

THE AUDIT'S FINDING, AS A TEST
------------------------------
`load_frozen_tasks` checked ids, counts and `frozen_at`. One task's `input.candidate` was then
replaced with that task's own hidden answer -- a held-out repair task turned into a copy of its
solution -- and the loader accepted it, because nothing tied the manifest to the CONTENT of the
records it named.

Every test below builds its own panel in `tmp_path` from the REAL freeze path
(`eval.repair_dataset_synth.freeze_manifest`), so none of them depends on the recorded dataset.
For each guard there is a test that it FIRES; for the boundary as a whole there is a positive
control (`test_an_intact_frozen_panel_loads_and_is_bound`) so that a loader which refuses
everything cannot look like a loader which refuses the right things.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import frozen_manifest as fm
from eval import repair_dataset_synth as rds

RECIPE_FIXTURE = {
    "target": "build/src/race/ui/race_ui_effects.o",
    "compiler": "ido-5.3",
    "command_sha256": "0" * 64,
    "makefile_sha256": "1" * 64,
    "projection_sha256": "2" * 64,
    "command": ["python3", "tools/asm-processor/build.py", "tools/ido-recomp/linux/cc"],
}


def _record(i: int, *, split: str = "test") -> dict:
    """One frozen-panel record in the real shape, with a candidate that is NOT its answer."""
    answer = (f'#include "common.h"\n\ns32 syn_f{i}(s32 arg0) {{\n'
              f'    s32 acc;\n\n    acc = arg0 * {i + 2};\n    return acc;\n}}\n')
    candidate = answer.replace(f"* {i + 2}", f"* {i + 3}")
    return {
        "schema_version": 2,
        "task_id": f"syn:unit:{i}:split-initialiser",
        "family": "unit", "seed": i, "mutation": "split-initialiser", "fault_axis": "regalloc",
        "function": f"syn_f{i}", "group": "unit", "source_kind": "synthetic", "split": split,
        "input": {"assembly": f"li v0,{i}\nmult v0,v1",
                  "candidate": candidate,
                  "feedback": {"kind": "instruction-diff",
                               "text": f"- li v0,{i}\n+ li v0,{i + 1}"}},
        "parent": {"compiled": True, "stderr": "", "score": 50.0, "asm": f"li v0,{i}",
                   "asm_sha256": rds.sha_text(f"li v0,{i}")},
        "target": {"asm": f"li v0,{i}", "exact": True,
                   "asm_sha256": rds.sha_text(f"li v0,{i}")},
        "child": {"exact": True, "source_c": answer, "outcome": "verified-repair"},
        "generator_source": answer,
        "provenance": {"built_at": 1},
    }


def _clone(row: dict) -> dict:
    return json.loads(json.dumps(row))


def _write_dataset(path: Path, rows: list[dict]) -> None:
    path.write_bytes(rds.dataset_bytes(rows))


def _rehash(manifest: dict, manifest_path: Path) -> dict:
    """Recompute a tampered manifest's own digest, so only the intended guard can fire."""
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = fm.manifest_content_sha256(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def _reforged(manifest: dict, manifest_path: Path, rows: list[dict], dataset: Path) -> dict:
    """A tamperer with write access: dataset rewritten AND every digest recomputed.

    This is the strongest form of the audit's swap. The dataset digest and the manifest's own
    digest both agree with the tampered state, so nothing but the per-record content hash can
    catch it -- which is exactly why that hash exists.
    """
    _write_dataset(dataset, rows)
    manifest["dataset_sha256"] = rds.sha_bytes(rds.dataset_bytes(rows))
    manifest["dataset_lines"] = len(rows)
    return _rehash(manifest, manifest_path)


def _panel(tmp_path, *, n: int = 4, split: str = "test", rows: list[dict] | None = None):
    """A self-consistent frozen fixture written by the REAL freeze path."""
    rows = rows if rows is not None else [_record(i, split=split) for i in range(n)]
    manifest_path = tmp_path / "FROZEN_SPLIT.json"
    manifest = rds.freeze_manifest(rows, manifest_path=manifest_path,
                                   recipe=dict(RECIPE_FIXTURE), test_families=())
    dataset = tmp_path / "tasks.jsonl"
    _write_dataset(dataset, rows)
    return manifest, manifest_path, dataset, rows


def _load(manifest_path: Path, dataset: Path, **kwargs):
    kwargs.setdefault("recipe", dict(RECIPE_FIXTURE))
    return fm.load_frozen_tasks(manifest_path, dataset, **kwargs)


# --- the positive control --------------------------------------------------------

def test_an_intact_frozen_panel_loads_and_is_bound(tmp_path):
    """Every guard below must be silent on a panel that is exactly what was frozen."""
    manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    assert manifest["schema_version"] == rds.MANIFEST_SCHEMA_VERSION
    assert set(manifest["record_hashes"]) == {r["task_id"] for r in rows}
    selected, loaded = _load(manifest_path, dataset)
    assert [t["task_id"] for t in selected] == [r["task_id"] for r in rows]
    assert loaded["manifest_sha256"] == manifest["manifest_sha256"]
    assert loaded["load_notes"] == []
    assert all(t.get("prompt_sha256") for t in selected), (
        "the loader must hash the exact prompt it validated at the model-input boundary")


def test_a_limit_selects_a_prefix_of_the_frozen_split(tmp_path):
    """`--limit` is how a smaller run is declared; it must not silently take other tasks."""
    _manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    selected, _loaded = _load(manifest_path, dataset, limit=2)
    assert [t["task_id"] for t in selected] == [r["task_id"] for r in rows[:2]]


# --- the swap the audit performed -------------------------------------------------

def test_a_post_freeze_candidate_swap_is_rejected(tmp_path):
    """THE AUDIT'S SWAP: `input.candidate` replaced by the task's own hidden answer.

    Both halves are checked. First with the dataset edited and nothing else -- the digest guard
    can see that. Then with EVERY digest recomputed, which is what a real tamperer would do: only
    the per-record content hash is left to catch it, and it does.
    """
    manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    swapped = [_clone(r) for r in rows]
    swapped[1]["input"]["candidate"] = swapped[1]["generator_source"]

    _write_dataset(dataset, swapped)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "not the dataset this manifest froze" in str(excinfo.value)

    _reforged(manifest, manifest_path, swapped, dataset)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    message = str(excinfo.value)
    assert "changed after it was frozen" in message, message
    assert rows[1]["task_id"] in message, "the refusal must name the task that was swapped"
    assert "solver-visible input" in message, (
        "the message must say WHICH binding caught it, not just that something did")


def test_a_post_freeze_answer_swap_is_rejected(tmp_path):
    """The mirror image: the supervision is replaced, the candidate left alone."""
    manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    swapped = [_clone(r) for r in rows]
    swapped[2]["child"]["source_c"] = "s32 syn_f2(s32 a) { return a; }\n"
    swapped[2]["generator_source"] = swapped[2]["child"]["source_c"]
    _reforged(manifest, manifest_path, swapped, dataset)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    message = str(excinfo.value)
    assert "the task's answer changed after it was frozen" in message, message


# --- the manifest's own integrity and the dataset file -----------------------------

def test_a_manifest_edited_after_freezing_is_rejected(tmp_path):
    """The manifest's own digest: a count quietly changed after the freeze."""
    manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    manifest["counts"]["test"] = 3
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "was modified after it was frozen" in str(excinfo.value)


def test_a_dataset_the_manifest_did_not_freeze_is_rejected(tmp_path):
    """Same records, different file bytes: the dataset digest is over the FILE, not a summary."""
    _manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    dataset.write_bytes(dataset.read_bytes() + b"\n")
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "is not the dataset this manifest froze" in str(excinfo.value)


def test_a_stale_manifest_without_content_hashes_is_refused_with_a_clear_message(tmp_path):
    """A pre-fix manifest cannot bind its records, so it must be refused, not accepted.

    This is the state of `eval/results/local-posttraining-20260920/dataset/FROZEN_SPLIT.json`
    after this fix: schema 2, ids and counts only. The message has to say what to do about it.
    """
    manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    for field in ("record_hashes", "dataset_sha256", "dataset_lines"):
        manifest.pop(field, None)
    manifest["schema_version"] = 2
    _rehash(manifest, manifest_path)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    message = str(excinfo.value)
    assert "PREDATES CONTENT HASHING" in message.upper(), message
    assert "Re-freeze" in message, message


# --- ids, splits and counts --------------------------------------------------------

def test_duplicate_ids_are_refused_by_the_freezer_and_by_the_loader(tmp_path):
    """One id, one record. A duplicate makes 'which candidate ran' ambiguous."""
    rows = [_record(i) for i in range(3)]
    rows.append(_clone(rows[0]))
    with pytest.raises(SystemExit) as excinfo:
        rds.freeze_manifest(rows, manifest_path=tmp_path / "F.json", recipe={},
                            test_families=())
    assert "more than once" in str(excinfo.value)

    manifest, manifest_path, dataset, clean = _panel(tmp_path, n=4)
    duplicated = [_clone(r) for r in clean] + [_clone(clean[0])]
    _reforged(manifest, manifest_path, duplicated, dataset)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "duplicated task id" in str(excinfo.value)


def test_a_task_in_two_splits_is_rejected(tmp_path):
    """Split drift: the same task held out AND trained on makes every number fiction."""
    manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    stolen = manifest["task_ids"]["test"][0]
    manifest["task_ids"]["train"] = sorted(manifest["task_ids"]["train"] + [stolen])
    manifest["counts"]["train"] = len(manifest["task_ids"]["train"])
    manifest["counts"]["total"] = (manifest["counts"]["train"] + manifest["counts"]["test"])
    _rehash(manifest, manifest_path)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "appear in two splits" in str(excinfo.value)


def test_duplicate_ids_inside_one_split_list_are_rejected(tmp_path):
    """A panel that names one task twice is ambiguous even if the dataset holds it once."""
    manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    manifest["task_ids"]["test"] = manifest["task_ids"]["test"] + [manifest["task_ids"]["test"][0]]
    manifest["counts"]["test"] = len(manifest["task_ids"]["test"])
    manifest["counts"]["total"] = manifest["counts"]["test"]
    _rehash(manifest, manifest_path)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "duplicate id(s) in split" in str(excinfo.value)


def test_counts_that_disagree_with_the_id_lists_are_rejected(tmp_path):
    manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    manifest["counts"]["test"] = 3
    _rehash(manifest, manifest_path)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "counts say 3 test task(s) but lists 4" in str(excinfo.value)


def test_a_record_whose_split_disagrees_with_its_listing_is_rejected(tmp_path):
    manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    rows[0]["split"] = "train"
    _reforged(manifest, manifest_path, rows, dataset)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "is listed in split 'test' but the record says 'train'" in str(excinfo.value)


def test_a_dataset_task_the_manifest_never_froze_is_rejected(tmp_path):
    manifest, manifest_path, dataset, rows = _panel(tmp_path, n=4)
    extra = _clone(rows[0])
    extra["task_id"] = "syn:unit:99:split-initialiser"
    _reforged(manifest, manifest_path, rows + [extra], dataset)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "the manifest never froze" in str(excinfo.value)


def test_a_split_the_manifest_did_not_freeze_is_rejected(tmp_path):
    _manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset, split="dev")
    assert "froze no 'dev' split" in str(excinfo.value)


# --- compiler identity --------------------------------------------------------------

def test_the_compiler_identity_recorded_in_the_manifest_is_checked(tmp_path):
    """A panel frozen against one compiler must not be evaluated with another."""
    _manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    other = dict(RECIPE_FIXTURE, command_sha256="f" * 64)
    with pytest.raises(SystemExit) as excinfo:
        fm.load_frozen_tasks(manifest_path, dataset, recipe=other)
    message = str(excinfo.value)
    assert "not the one being used" in message and "command_sha256" in message, message


def test_a_makefile_change_that_leaves_the_command_alone_is_a_note_not_a_refusal(tmp_path):
    """The check must fire on a different COMPILER, not on any difference at all."""
    _manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4)
    moved = dict(RECIPE_FIXTURE, makefile_sha256="9" * 64)
    _selected, loaded = fm.load_frozen_tasks(manifest_path, dataset, recipe=moved)
    assert any("makefile_sha256 differs" in note for note in loaded["load_notes"]), loaded["load_notes"]


# --- the model-input boundary --------------------------------------------------------

def test_the_leakage_check_runs_on_the_final_rendered_prompt(tmp_path):
    """The dataset-time check proved the RECORD was clean. This check is on the prompt itself."""
    task = _record(0)
    prompt = fm.task_prompt(task)
    assert task["input"]["candidate"] in prompt
    assert task["generator_source"].strip() not in prompt

    tampered = _clone(task)
    tampered["input"]["candidate"] = tampered["generator_source"]
    with pytest.raises(SystemExit) as excinfo:
        fm.task_prompt(tampered)
    assert "leaks its answer" in str(excinfo.value)

    echoed = _clone(task)
    echoed["input"]["feedback"]["text"] = ("here is the expected answer:\n"
                                           + echoed["generator_source"])
    with pytest.raises(SystemExit) as excinfo:
        fm.task_prompt(echoed)
    assert "leaks its answer" in str(excinfo.value)


def test_a_record_with_no_answer_cannot_be_checked_and_is_refused():
    """`nothing to check` is not `no leak`: without the answer the check is vacuous."""
    task = _record(0)
    task["generator_source"] = ""
    task["child"]["source_c"] = ""
    with pytest.raises(SystemExit) as excinfo:
        fm.task_prompt(task)
    assert "carries no answer" in str(excinfo.value)


def test_the_loader_validates_every_task_prompt_before_returning(tmp_path):
    """A panel frozen WITH a leak in it is refused at load, before the model is touched.

    The content hashes cannot catch this one: the record IS the record that was frozen, so every
    digest agrees. Only rendering the prompt and checking it can, which is why the check runs at
    the model-input boundary and not only at dataset build time.
    """
    rows = [_record(i) for i in range(4)]
    rows[3]["input"]["feedback"]["text"] = "expected answer:\n" + rows[3]["generator_source"]
    _manifest, manifest_path, dataset, _rows = _panel(tmp_path, n=4, rows=rows)
    with pytest.raises(SystemExit) as excinfo:
        _load(manifest_path, dataset)
    assert "leaks its answer" in str(excinfo.value)


def test_the_evaluator_renders_the_prompt_through_a_checked_boundary():
    """Wiring: whatever module owns the check, `evaluate_arm` must go through it."""
    from eval import evaluate_source_repair as ev
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert "prompt = task_prompt(task)" in source, (
        "evaluate_arm must render the model input through the checked boundary")

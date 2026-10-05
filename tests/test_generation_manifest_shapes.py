"""`verify()` against the shapes the FREEZER actually emits, not against the shape it wished for.

WHAT WENT WRONG. `eval/generation_manifest.verify()` assumed every component was one openable file with a
`path` and a digest. Two of the shapes `eval/rsi_loop.stage_frozen` writes are not that:

  * `base_model={"path": <a DIRECTORY>, "files": [...]}`      -> the directory was opened as a file
  * `prompts={"system": {"sha256": <hex>, "chars": N}}`       -> `KeyError: 'path'`

and `compositions` (a list, unlike the dict groups the old loop iterated) was never looked at at all. A
caller that wrapped `verify()` read the crash as "not verified"; a caller that did not crashed the stage.

Every test here drives the REAL `freeze()`/`verify()` pair on a scratch tree. Where a payload is one the
current COORDINATOR does not emit, the test says so in its docstring and says where the shape comes from
instead -- the `Generation` dataclass contract, or `eval.rsi_interventions.Composition`, both of which
`freeze()` writes without complaint. Nothing here is hand-written into a manifest file, except the
tampering a tamper check exists to catch.

The whole point of the report is that it distinguishes THREE states, so most assertions are on `status`:
`verified` (checked by content, nothing moved), `violated` (something moved or is missing) and
`unverified` (nothing is known to have moved, but an identity cannot be recomputed from the manifest).
`verified` is True only for the first; a report that folded the third into it would be the defect.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from eval import generation_manifest as gm
from eval.rsi_interventions import Composition

CREATED_AT = "2026-09-21T00:00:00Z"


@pytest.fixture
def sandbox():
    """A scratch tree from `tempfile.mkdtemp`, matching `tests/test_rsi_foundations.py`.

    The repo's `tmp_path` fixture raises `PermissionError` while scanning `%TEMP%\\pytest-of-grant` on
    this box unless `--basetemp` is passed, and a test file that only runs under one flag is a trap.
    """
    path = Path(tempfile.mkdtemp(prefix="manifest-shapes-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _generation(gen_id: str, **components) -> gm.Generation:
    return gm.Generation(id=gen_id, created_at=CREATED_AT, **components)


def _model_tree(sandbox: Path, name: str = "model") -> tuple[Path, list[dict]]:
    """A base-model directory and the file list the coordinator builds for it.

    `stage_frozen` does `sorted(base.glob("*.safetensors") + base.glob("*.json"))` and hashes each, so the
    list is top-level weights plus configs -- NOT the whole tree. `hash_tree` is the other half.
    """
    base = sandbox / name
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text('{"model_type": "qwen2"}', encoding="utf-8")
    (base / "model.safetensors").write_bytes(b"stand-in weights")
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")
    files = sorted([*base.glob("*.safetensors"), *base.glob("*.json")])
    return base, [gm.hash_artifact(path) for path in files]


def _coordinator_generation(sandbox: Path, gen_id: str = "S0", **overrides) -> gm.Generation:
    """The component set `eval/rsi_loop.stage_frozen` emits, on scratch paths.

    Mirrored from that method rather than invented: directory model + file list, an adapters list, two
    inline digests, an absent memory artifact, an empty compositions list, verifier/evaluator/dataset
    files and a budgets dict.
    """
    base, base_files = _model_tree(sandbox)
    adapter = sandbox / "adapter"
    adapter.mkdir(parents=True, exist_ok=True)
    (adapter / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter / "adapter_model.bin").write_bytes(b"adapter weights")
    evaluator = sandbox / "rsi_transfer.py"
    evaluator.write_text("# the evaluator\n", encoding="utf-8")
    splits = sandbox / "splits.json"
    splits.write_text('{"dev": ["f1"], "test": ["f2"]}', encoding="utf-8")
    payload = dict(
        base_model={"path": str(base), "files": base_files},
        adapters=[gm.hash_artifact(adapter / "adapter_config.json"),
                  gm.hash_artifact(adapter / "adapter_model.bin")],
        prompts={"system": {"sha256": gm.sha256_text("SYSTEM PROMPT"), "chars": 13}},
        tool_schema={"action_space": {"sha256": gm.sha256_text('{"redraft": 1}')}},
        # `stage_frozen` writes the notebook's absence AND its intent in the same record; the intent is
        # what keeps S0's empty memory from reading as a vanished artifact.
        memory={"path": str(sandbox / "notes.jsonl"), "exists": False,
                "absent_because": "no note has been written yet; S0 starts with an empty verified memory"},
        compositions=[],
        verifier={"path": str(sandbox / "workspace.py"),
                  **gm.hash_artifact(sandbox / "workspace.py")},
        evaluator={"path": str(evaluator), **gm.hash_artifact(evaluator)},
        datasets={"splits": gm.hash_artifact(splits)},
        budgets={"model_calls": 12, "compiles": 72},
        notes="agent_version=1",
    )
    (sandbox / "workspace.py").write_text("# the verifier\n", encoding="utf-8")
    # `verifier` hashes workspace.py, which is written above; rebuild it now that the file exists.
    payload["verifier"] = {"path": str(sandbox / "workspace.py"),
                           **gm.hash_artifact(sandbox / "workspace.py")}
    payload.update(overrides)
    return _generation(gen_id, **payload)


# --------------------------------------------------------------------------- #
# THE SHAPES THE FREEZER EMITS
# --------------------------------------------------------------------------- #

def test_the_directory_model_shape_verifies_and_a_changed_file_in_the_tree_fails(sandbox):
    """`base_model={"path": <dir>, "files": [...]}` -- the shape that made `verify()` raise.

    `hash_tree()` exists in the module for exactly this and the old `verify()` never called it.
    """
    out = sandbox / "manifests"
    base, files = _model_tree(sandbox)
    generation = _generation("directory-model", base_model={"path": str(base), "files": files})
    gm.freeze(out, generation)

    report = gm.verify(out, "directory-model")
    assert report["problems"] == []
    assert report["status"] == "verified" and report["verified"] is True
    assert report["generation"] == "directory-model"
    assert report["fingerprint"] == generation.content_fingerprint()
    # every listed file was compared, with the rule the freezer recorded for it
    assert [item["kind"] for item in report["checked"] if item["kind"] == "file"]
    assert all(item["hash_mode"] == "full" for item in report["checked"] if item["kind"] == "file")

    (base / "config.json").write_text('{"model_type": "llama"}', encoding="utf-8")
    report = gm.verify(out, "directory-model")
    assert report["verified"] is False and report["status"] == "violated"
    assert f"content changed: {base / 'config.json'}" in report["problems"]


def test_a_file_added_to_the_frozen_tree_is_reported_as_unrecorded_not_as_a_pass(sandbox):
    """A NEW file in the model directory has no frozen digest, so it is unverifiable -- not fine.

    The frozen list is a glob of top-level weights and configs; a file outside it cannot be compared
    against anything. Reporting it as verified would be the silent decline this project keeps catching,
    and calling it a violation would cry wolf on every legitimate addition to a model directory.
    """
    out = sandbox / "manifests"
    base, files = _model_tree(sandbox)
    gm.freeze(out, _generation("tree-added", base_model={"path": str(base), "files": files}))
    assert gm.verify(out, "tree-added")["status"] == "verified"

    (base / "added_after_freeze.json").write_text("{}", encoding="utf-8")

    report = gm.verify(out, "tree-added")
    assert report["problems"] == []                        # nothing recorded moved
    assert report["verified"] is False and report["status"] == "unverified"
    unrecorded = [entry for entry in report["unverifiable"] if entry["kind"] == "unrecorded-tree-file"]
    assert [Path(entry["path"]).name for entry in unrecorded] == ["added_after_freeze.json"]


def test_the_inline_prompt_shape_is_reported_unverifiable_by_content_not_verified(sandbox):
    """`prompts={"system": {"sha256": ..., "chars": N}}` -- the shape that produced `KeyError: 'path'`.

    There is no file and no text in the payload, so nothing can be recomputed. The honest report is
    "unverified", naming the digest it holds for whoever has the other end of it -- NOT a pass, which is
    the defect this file exists for, and not an exception either.
    """
    out = sandbox / "manifests"
    digest = gm.sha256_text("prompt")
    gm.freeze(out, _generation("inline-prompt", prompts={"system": {"sha256": digest, "chars": 6}}))

    report = gm.verify(out, "inline-prompt")
    assert report["problems"] == []                        # nothing is KNOWN to have moved
    assert report["verified"] is False                     # ...and that is not the same as verified
    assert report["status"] == "unverified"
    entry = next(item for item in report["unverifiable"] if item["component"] == "prompts.system")
    assert entry["kind"] == "inline-digest"
    assert entry["recorded_sha256"] == digest
    assert entry["chars"] == 6
    assert "no content to recompute" in entry["reason"]
    # the manifest body itself IS checked, and the report says so rather than leaving it ambiguous
    assert entry["frozen_manifest_intact"] is True
    assert report["manifest"]["mode"] == "verified"


def test_the_coordinator_shape_verifies_without_raising_and_names_what_it_cannot_check(sandbox):
    """The whole `stage_frozen` component set at once: this is the call that used to raise.

    It must return a report, must not claim the inline prompt and action-space digests were verified, and
    must have checked every file-backed component including the ones the old loop skipped.

    S0's empty notebook is declared-absent-with-intent, so it is checked; the two inline digests are the
    only unverifiable components, and that is why the status is `unverified` rather than `verified`. The
    same shape WITHOUT the declared intent is a violation -- pinned at the end, and by
    `test_a_declared_absent_artifact_is_consistent_or_a_problem`.
    """
    out = sandbox / "manifests"
    generation = _coordinator_generation(sandbox)
    gm.freeze(out, generation)

    report = gm.verify(out, "S0")

    assert report["verified"] is False and report["status"] == "unverified"
    assert report["problems"] == []
    assert report["fingerprint"] == generation.content_fingerprint()
    unverifiable = {entry["component"] for entry in report["unverifiable"]}
    assert unverifiable == {"prompts.system", "tool_schema.action_space"}
    checked_paths = {Path(item["path"]).name for item in report["checked"] if item.get("path")}
    assert {"config.json", "model.safetensors", "tokenizer.json", "adapter_config.json",
            "adapter_model.bin", "rsi_transfer.py", "splits.json", "workspace.py"} <= checked_paths
    assert report["hash_modes"] == {"full": 8}             # and the mode used for each is recorded
    assert report["components"]["memory"] == "checked"
    assert any(item["kind"] == "declared-absent" for item in report["checked"])

    # ...and the pre-change emitter, which recorded the same absence without stating the intent
    legacy = _coordinator_generation(sandbox, gen_id="S0-legacy")
    legacy.memory = {"path": str(sandbox / "legacy-notes.jsonl"), "exists": False}
    gm.freeze(sandbox / "legacy-manifests", legacy)
    report = gm.verify(sandbox / "legacy-manifests", "S0-legacy")
    assert report["status"] == "violated"
    assert report["problems"] == [f"missing artifact: {sandbox / 'legacy-notes.jsonl'}"]


def test_a_declared_absent_artifact_is_consistent_or_a_problem(sandbox):
    """`{"exists": False}` carries two meanings, and the record now says which. All three directions.

    `hash_artifact()` returns that shape for a path the freezer was ASKED to hash and could not read, and
    for an adapter or a verifier file that absence means the generation is not what the record says it is.
    The one legitimate absence -- S0's empty notebook -- is the one the emitter can declare in advance:

      * intent + still absent   -> consistent with the record, CHECKED as declared-absent
      * intent + now present    -> VIOLATED ("artifact appeared since freeze")
      * no intent + still absent -> VIOLATED ("missing artifact"): the record asked for a file and there
        is none, and nothing in it says the absence was intended
    """
    out = sandbox / "manifests"
    notes = sandbox / "notes.jsonl"
    declared = {"path": str(notes), "exists": False,
                "absent_because": "no note has been written yet; S0 starts with an empty verified memory"}

    gm.freeze(out, _generation("declared-absent", memory=dict(declared)))
    report = gm.verify(out, "declared-absent")
    assert report["problems"] == []
    assert report["status"] == "verified" and report["verified"] is True
    assert report["components"]["memory"] == "checked"
    assert {"component": "memory", "kind": "declared-absent", "path": str(notes),
            "absent_because": declared["absent_because"]} in report["checked"]

    notes.write_text('{"id": "note-1"}\n', encoding="utf-8")
    report = gm.verify(out, "declared-absent")
    assert report["verified"] is False and report["status"] == "violated"
    assert report["problems"] == [f"artifact appeared since freeze: {notes}"]

    # the record that asked for a file and did not say why there is none: a violation, named
    absent = sandbox / "never-created.jsonl"
    gm.freeze(out, _generation("undeclared-absent", memory={"path": str(absent), "exists": False}))
    report = gm.verify(out, "undeclared-absent")
    assert report["status"] == "violated" and report["verified"] is False
    assert report["problems"] == [f"missing artifact: {absent}"]


def test_a_notebook_written_after_the_freeze_is_an_appeared_artifact(sandbox):
    """S1's memory is a file entry; S0's is a declared-absent path. Both shapes, and the appearing one."""
    out = sandbox / "manifests"
    notes = sandbox / "notes.jsonl"
    gm.freeze(out, _generation("memory-absent",
                               memory={"path": str(notes), "exists": False,
                                       "absent_because": "no note has been written yet"}))
    assert gm.verify(out, "memory-absent")["status"] == "verified"

    notes.write_text('{"id": "note-1"}\n', encoding="utf-8")
    report = gm.verify(out, "memory-absent")
    assert f"artifact appeared since freeze: {notes}" in report["problems"]

    # the S1 shape: a real hashed file plus an extra field the walker must not choke on
    other = sandbox / "other-manifests"
    gm.freeze(other, _generation("memory-present",
                                 memory={**gm.hash_artifact(notes), "confirmed_notes": 1}))
    assert gm.verify(other, "memory-present")["status"] == "verified"
    notes.write_text('{"id": "note-1"}\n{"id": "note-2"}\n', encoding="utf-8")
    report = gm.verify(other, "memory-present")
    assert report["status"] == "violated"
    assert f"content changed: {notes}" in report["problems"]


# --------------------------------------------------------------------------- #
# EXECUTION-RELEVANT COMPONENTS THAT WERE NEVER CHECKED
# --------------------------------------------------------------------------- #

def test_a_changed_compositions_entry_fails_the_frozen_manifest(sandbox):
    """`compositions` is inline data with no file to rehash, and the old loop never looked at it.

    NOTE THE PAYLOAD: the coordinator currently emits `compositions=[]`, so this entry shape comes from
    `eval.rsi_interventions.Composition.as_dict()` -- the dataclass the field is declared over -- fed
    through the real `freeze()`/`verify()` pair. `freeze()` accepts it because a `Generation` is the
    freezer's own contract, not a coordinator-only template.

    A change to it is caught by the freeze-time digest of the manifest bytes. The report names the FILE,
    not the field: without keeping a second copy of the frozen manifest there is no way to say which
    field moved, and the strongest true statement available is the one that is made.
    """
    out = sandbox / "manifests"
    composition = Composition(id="comp-1", when={"residual_kind": "structural"},
                              prefer=["redraft", "permute"], reason="measured: shifts first",
                              status="confirmed", created_at=0.0).as_dict()
    gm.freeze(out, _generation("comp-frozen", compositions=[composition]))
    assert gm.verify(out, "comp-frozen")["status"] == "verified"

    manifest = out / "comp-frozen.json"
    payload = json.loads(manifest.read_text("utf-8"))
    payload["compositions"][0]["prefer"] = ["redraft", "stop"]
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = gm.verify(out, "comp-frozen")
    assert report["verified"] is False and report["status"] == "violated"
    assert f"manifest content changed since it was frozen: {manifest}" in report["problems"]


def test_a_changed_budgets_entry_fails_the_frozen_manifest(sandbox):
    """Same mechanism, the other inline component the audit named: nothing outside hashes a budget dict."""
    out = sandbox / "manifests"
    gm.freeze(out, _generation("budget-frozen", budgets={"model_calls": 12, "compiles": 72}))
    assert gm.verify(out, "budget-frozen")["status"] == "verified"

    manifest = out / "budget-frozen.json"
    payload = json.loads(manifest.read_text("utf-8"))
    payload["budgets"]["model_calls"] = 120
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = gm.verify(out, "budget-frozen")
    assert report["status"] == "violated"
    assert f"manifest content changed since it was frozen: {manifest}" in report["problems"]


def test_a_manifest_without_a_freeze_digest_reports_inline_content_as_unverifiable(sandbox):
    """Manifests frozen before the digest existed must not suddenly pass.

    Deleting the `.sha256` sidecar is the state of every manifest written by the previous version. With
    no digest to compare against, `budgets` and `compositions` can only be reported as unverifiable --
    which is what the report does, instead of treating "no path" as "nothing to check".
    """
    out = sandbox / "manifests"
    gm.freeze(out, _generation("legacy", budgets={"compiles": 72},
                               compositions=[{"id": "comp-1", "prefer": ["redraft"]}]))
    assert gm.verify(out, "legacy")["status"] == "verified"

    gm.manifest_digest_path(out / "legacy.json").unlink()

    report = gm.verify(out, "legacy")
    assert report["problems"] == []
    assert report["status"] == "unverified" and report["verified"] is False
    assert report["manifest"]["mode"] == "not-recorded"
    inline = {entry["component"] for entry in report["unverifiable"]
              if entry["kind"] == "manifest-inline"}
    assert {"budgets.compiles", "compositions[0].id", "compositions[0].prefer[0]"} <= inline
    assert all("no freeze-time manifest digest" in entry["reason"] for entry in report["unverifiable"])


def test_a_rewritten_digest_record_is_a_violation_not_a_pass(sandbox):
    """The record must agree with the bytes, or there is nothing to trust it against."""
    out = sandbox / "manifests"
    gm.freeze(out, _generation("digest-tampered", budgets={"compiles": 72}))
    gm.manifest_digest_path(out / "digest-tampered.json").write_text("0" * 64 + "\n", encoding="utf-8")

    report = gm.verify(out, "digest-tampered")
    assert report["status"] == "violated"
    assert f"manifest content changed since it was frozen: {out / 'digest-tampered.json'}" \
        in report["problems"]


# --------------------------------------------------------------------------- #
# MISSING AND TAMPERED ARTIFACTS, AND WHICH HASH RULE WAS USED
# --------------------------------------------------------------------------- #

def test_a_missing_artifact_is_a_problem_and_never_a_crash(sandbox):
    """Both ways to be missing: the freezer could not read it at freeze time, and it vanished afterwards.

    Both are violations. The one absence that is NOT a problem is the one the emitter declared in advance
    (`absent_because`), pinned by `test_a_declared_absent_artifact_is_consistent_or_a_problem`.
    """
    out = sandbox / "manifests"

    absent = sandbox / "never-written.bin"
    gm.freeze(out, _generation("gen-absent", verifier=gm.hash_artifact(absent)))
    report = gm.verify(out, "gen-absent")
    assert report["verified"] is False and report["status"] == "violated"
    assert report["problems"] == [f"missing artifact: {absent}"]

    deleted = sandbox / "later-deleted.bin"
    deleted.write_bytes(b"v1")
    gm.freeze(out, _generation("gen-deleted", verifier=gm.hash_artifact(deleted)))
    assert gm.verify(out, "gen-deleted")["status"] == "verified"
    deleted.unlink()
    report = gm.verify(out, "gen-deleted")
    assert report["status"] == "violated"
    assert f"missing artifact: {deleted}" in report["problems"]

    # a generation nobody froze is a report too, not a traceback out of a caller's stage
    report = gm.verify(out, "never-frozen")
    assert report["status"] == "violated" and report["fingerprint"] is None
    assert report["problems"] == [f"missing manifest: {out / 'never-frozen.json'}"]


def test_a_tampered_small_artifact_fails_by_full_hash_and_says_which_mode_was_used(sandbox):
    """The positive tamper check, with the `hash_mode` claim stated rather than assumed."""
    out = sandbox / "manifests"
    weights = sandbox / "adapter.bin"
    weights.write_bytes(b"adapter-weights-v1")
    entry = gm.hash_artifact(weights)
    assert entry["hash_mode"] == "full"
    gm.freeze(out, _generation("gen-small", adapters=[entry]))

    report = gm.verify(out, "gen-small")
    assert report["status"] == "verified"
    assert report["hash_modes"] == {"full": 1}
    assert {"component": "adapters[0]", "kind": "file", "path": str(weights),
            "hash_mode": "full", "bytes": len(b"adapter-weights-v1")} in report["checked"]

    weights.write_bytes(b"adapter-weights-v2")
    report = gm.verify(out, "gen-small")
    assert report["status"] == "violated"
    assert report["problems"] == [f"content changed: {weights}"]


def test_a_sampled_artifact_is_still_compared_with_the_sampled_rule(sandbox, monkeypatch):
    """A big file was hashed by SAMPLE, so re-verification must sample it too -- and say so.

    Re-deciding the rule at verify time would compare a different claim than the manifest records. This
    pins both halves: a change OUTSIDE the sampled region is invisible (the documented trade of the
    sampled rule, and proof the sampled rule was actually used rather than a full digest), and a change
    INSIDE it fails. `FULL_HASH_LIMIT`/`SAMPLE_BYTES` are monkeypatched so the "large" file is ten bytes;
    the freezer and the re-verification run under the same constants, which is what production does.
    """
    monkeypatch.setattr(gm, "FULL_HASH_LIMIT", 8)
    monkeypatch.setattr(gm, "SAMPLE_BYTES", 4)

    out = sandbox / "manifests"
    checkpoint = sandbox / "model.safetensors"
    checkpoint.write_bytes(b"0123456789")
    entry = gm.hash_artifact(checkpoint)
    assert entry["hash_mode"] == gm.SAMPLED_HASH_MODE
    assert entry["bytes"] == 10
    gm.freeze(out, _generation("gen-sampled", base_model=entry))

    report = gm.verify(out, "gen-sampled")
    assert report["status"] == "verified"
    assert report["hash_modes"] == {gm.SAMPLED_HASH_MODE: 1}
    assert report["checked"][0]["hash_mode"] == gm.SAMPLED_HASH_MODE

    checkpoint.write_bytes(b"0123XX6789")                 # outside the first and last sample
    report = gm.verify(out, "gen-sampled")
    assert report["status"] == "verified", report["problems"]

    checkpoint.write_bytes(b"X123456789")                 # inside the first sample
    report = gm.verify(out, "gen-sampled")
    assert report["status"] == "violated"
    assert report["problems"] == [f"content changed: {checkpoint}"]


def test_a_sampled_artifact_whose_size_moved_fails_even_outside_the_sample(sandbox, monkeypatch):
    """The recorded size is part of a sampled identity, so a size change cannot ride along unnoticed.

    THE COLLISION HERE IS CONSTRUCTED, not found in the wild: with `SAMPLE_BYTES=4` the two files below
    have identical first and last samples at different total sizes, so the sampled DIGEST is blind to the
    change and the recorded `bytes` is the only thing that sees it. That is exactly why the size field is
    compared -- and a test that only compared digests would pass while the check did nothing.
    """
    monkeypatch.setattr(gm, "FULL_HASH_LIMIT", 8)
    monkeypatch.setattr(gm, "SAMPLE_BYTES", 4)

    out = sandbox / "manifests"
    checkpoint = sandbox / "weights.safetensors"
    checkpoint.write_bytes(b"AAAA00ABAB")                  # 10 bytes: first "AAAA", last "ABAB"
    entry = gm.hash_artifact(checkpoint)
    assert entry["bytes"] == 10 and entry["hash_mode"] == gm.SAMPLED_HASH_MODE
    gm.freeze(out, _generation("gen-size", base_model=entry))
    assert gm.verify(out, "gen-size")["status"] == "verified"

    checkpoint.write_bytes(b"AAAA00ABABAB")                # 12 bytes: first "AAAA", last "ABAB"
    assert gm.hash_artifact(checkpoint)["sha256"] == entry["sha256"]      # the digest cannot see it

    report = gm.verify(out, "gen-size")
    assert report["status"] == "violated"
    assert report["problems"] == [f"content changed: {checkpoint} (size 10 -> 12)"]


def test_verify_covers_every_field_of_the_generation_so_none_is_silently_unverified(sandbox):
    """The report names a status for every component, including the ones with nothing in them.

    "A mechanism that silently declines looks identical to one with nothing to do": an empty
    `compositions` and a `compositions` nobody checked look the same from outside unless the report says
    which is which.
    """
    out = sandbox / "manifests"
    gm.freeze(out, _generation("components", budgets={"compiles": 1}, compositions=[],
                               adapters=[], prompts={}, tool_schema={}, memory={}, verifier={},
                               evaluator={}, training={}, datasets={}, proposals=[], notes=""))

    report = gm.verify(out, "components")
    assert set(report["components"]) == set(gm.Generation(id="x", created_at="y").as_dict()) - {
        "id", "created_at"}
    assert report["components"]["compositions"] == "empty"
    assert report["components"]["budgets"] == "checked"     # inline data covered by the manifest digest
    assert report["status"] == "verified"

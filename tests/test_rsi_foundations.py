"""The foundations the narrow-RSI experiment stands on: one frozen generation, one shared budget, one
demand queue that cannot see the held-out answers.

Each of the three modules makes a claim that is cheap to check and expensive to get wrong:

  * `eval/generation_manifest.py` -- "a generation whose bytes have moved is refused rather than silently
    reused". A tamper check that passes on tampered bytes is worse than no tamper check, because the
    experiment would cite a generation that no longer exists.
  * `eval/budget_ledger.py` -- one EXPERIMENT-wide budget, idempotent on resume. A resumed run that
    re-charges a stage, or resets a spent budget, makes the declared ceiling a decoration.
  * `eval/research_demand.py` -- deterministic features and split discipline. A queue that leaks a
    held-out name into the file the researcher reads is contamination with extra steps.

Every test builds its own scratch tree with `tempfile.mkdtemp`. The repo's `tmp_path` fixture raises
`PermissionError` while scanning `%TEMP%\\pytest-of-grant` on this box, so using it would error this file
before a single assertion ran.

Three tests are `xfail(strict=False)`: they pin docstring claims the modules do not currently satisfy.
They are findings with a reproducer rather than noise -- each names the line, the symptom, and turns into
an XPASS if the module is fixed.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from eval import budget_ledger as bl
from eval import generation_manifest as gm
from eval import research_demand as rd

CREATED_AT = "2026-09-21T00:00:00Z"
REPO_ROOT = Path(rd.__file__).resolve().parents[1]


@pytest.fixture
def sandbox():
    """A scratch tree built here rather than by `tmp_path`, which is broken on this machine.

    Same reasoning as `tests/test_tool_action_sft.py`: pytest's `tmp_path` errors out before the test
    body runs, and `tempfile.mkdtemp` works, so the plain `python -m pytest` command stays usable.
    """
    path = Path(tempfile.mkdtemp(prefix="rsi-foundations-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _generation(gen_id: str, *, created_at: str = CREATED_AT, **components) -> gm.Generation:
    """A generation with only the components a test cares about set; the rest stay at their defaults."""
    payload = {"notes": "baseline"}
    payload.update(components)
    return gm.Generation(id=gen_id, created_at=created_at, **payload)


# --------------------------------------------------------------------------- #
# GENERATION MANIFEST
# --------------------------------------------------------------------------- #

def test_a_frozen_generation_verifies_immediately(sandbox):
    """The positive control: without it, a `verify()` that refuses everything would look correct."""
    out = sandbox / "manifests"
    weights = sandbox / "adapter.bin"
    weights.write_bytes(b"adapter-weights-v1")

    generation = _generation("gen-000", base_model=gm.hash_artifact(weights))
    manifest = gm.freeze(out, generation)

    assert manifest == out / "gen-000.json"
    assert manifest.exists()
    # what was frozen is what comes back: components survive the JSON round trip unchanged
    assert gm.load(out, "gen-000").as_dict() == generation.as_dict()

    result = gm.verify(out, "gen-000")
    assert result["verified"] is True
    assert result["problems"] == []
    assert result["generation"] == "gen-000"
    assert result["fingerprint"] == generation.content_fingerprint()

    # `hash_mode` records WHICH digest was taken, so the weaker claim is never assumed
    entry = gm.load(out, "gen-000").base_model
    assert entry["hash_mode"] == "full"
    assert entry["sha256"] == gm.sha256_file(weights)
    assert entry["bytes"] == len(b"adapter-weights-v1")


def test_tampering_with_a_referenced_artifact_fails_verify_and_names_the_path(sandbox):
    """The artifact-tamper detection the experiment depends on.

    The manifest records a hash of the bytes it froze; changing the bytes underneath it must be refused
    AND must say which path moved, because a bare False cannot be acted on.
    """
    out = sandbox / "manifests"
    weights = sandbox / "adapter.bin"
    weights.write_bytes(b"adapter-weights-v1")
    gm.freeze(out, _generation("gen-000", base_model=gm.hash_artifact(weights)))
    assert gm.verify(out, "gen-000")["verified"] is True

    weights.write_bytes(b"adapter-weights-v2")

    result = gm.verify(out, "gen-000")
    assert result["verified"] is False
    assert result["problems"] == [f"content changed: {weights}"]


def test_refreezing_the_same_id_is_idempotent_and_different_content_raises(sandbox):
    """Immutability: a generation pointer must not be able to hide a silent overwrite."""
    out = sandbox / "manifests"
    weights = sandbox / "adapter.bin"
    weights.write_bytes(b"adapter-weights-v1")

    first = gm.freeze(out, _generation("gen-000", base_model=gm.hash_artifact(weights)))
    frozen_bytes = first.read_text("utf-8")

    # identical content: idempotent, same path, nothing rewritten
    twin = gm.freeze(out, _generation("gen-000", base_model=gm.hash_artifact(weights)))
    assert twin == first
    assert first.read_text("utf-8") == frozen_bytes

    # one changed component is a different generation, not an update
    with pytest.raises(FileExistsError) as excinfo:
        gm.freeze(out, _generation("gen-000", base_model=gm.hash_artifact(weights), notes="different"))
    assert "immutable" in str(excinfo.value)
    assert first.read_text("utf-8") == frozen_bytes      # the failed freeze did not overwrite anything

    # the same generation id under a DIFFERENT directory is a separate artifact, not a collision
    other = sandbox / "other-manifests"
    assert gm.freeze(other, _generation("gen-000")).name == "gen-000.json"


def test_content_fingerprint_ignores_id_and_created_at_but_tracks_every_component():
    """The id can be DERIVED from the fingerprint, so the fingerprint must not depend on the id."""
    verifier = {"name": "oracle", "sha256": "a" * 64}
    reference = _generation("gen-000", created_at="2026-01-01T00:00:00Z", verifier=verifier)
    renamed = _generation("gen-999", created_at="2027-12-31T23:59:59Z", verifier=verifier)

    assert reference.content_fingerprint() == renamed.content_fingerprint()
    assert len(reference.content_fingerprint()) == 16

    # a change to any fixed component moves it, including one nested inside a dict or list component
    changed = [
        _generation("gen-000", verifier={"name": "oracle", "sha256": "b" * 64}),
        _generation("gen-000", budgets={"compiles": 72}),
        _generation("gen-000", adapters=[{"step": 500}]),
        _generation("gen-000", prompts={"system": "v2"}),
        _generation("gen-000", notes="a different note"),
    ]
    fingerprints = {reference.content_fingerprint()} | {g.content_fingerprint() for g in changed}
    assert len(fingerprints) == 1 + len(changed)


def test_a_missing_artifact_is_a_problem_not_a_crash(sandbox):
    """Two ways to be missing: declared absent at freeze time, and deleted afterwards."""
    out = sandbox / "manifests"

    absent = sandbox / "never-written.bin"
    gm.freeze(out, _generation("gen-absent", verifier=gm.hash_artifact(absent)))
    result = gm.verify(out, "gen-absent")
    assert result["verified"] is False
    assert result["problems"] == [f"missing artifact: {absent}"]

    deleted = sandbox / "later-deleted.bin"
    deleted.write_bytes(b"v1")
    gm.freeze(out, _generation("gen-deleted", verifier=gm.hash_artifact(deleted)))
    assert gm.verify(out, "gen-deleted")["verified"] is True
    deleted.unlink()
    result = gm.verify(out, "gen-deleted")
    assert result["verified"] is False
    assert any(str(deleted) in problem for problem in result["problems"])


def test_hash_modes_are_recorded_and_match_what_was_actually_hashed(sandbox, monkeypatch):
    """The docstring states the split claim rather than glossing it: full for small artifacts, first and
    last sample plus size for weights too big to digest, and `hash_mode` says which one was used."""
    monkeypatch.setattr(gm, "FULL_HASH_LIMIT", 8)
    monkeypatch.setattr(gm, "SAMPLE_BYTES", 4)

    small = sandbox / "config.json"
    small.write_bytes(b"abcd")
    small_entry = gm.hash_artifact(small)
    assert small_entry["hash_mode"] == "full"
    assert small_entry["sha256"] == gm.sha256_file(small)

    big = sandbox / "checkpoint.safetensors"
    big.write_bytes(b"0123456789")
    big_entry = gm.hash_artifact(big)
    assert big_entry["hash_mode"] == "first+last-1MiB+size"
    assert big_entry["bytes"] == 10

    # bytes outside the first and last sample are not covered -- that is the documented trade
    middle_only = sandbox / "middle.safetensors"
    middle_only.write_bytes(b"0123XX6789")
    assert gm.hash_artifact(middle_only)["sha256"] == big_entry["sha256"]

    # ...and the size IS covered, so a truncated checkpoint cannot pass as the sampled one
    bigger = sandbox / "bigger.safetensors"
    bigger.write_bytes(b"0123456789X")
    assert gm.hash_artifact(bigger)["sha256"] != big_entry["sha256"]


def test_hash_tree_is_stable_and_skips_the_weight_blobs(sandbox):
    """A tree digest that depended on directory iteration order would make two identical trees differ."""
    root = sandbox / "generation"
    (root / "nested").mkdir(parents=True)
    (root / "a.json").write_text("{}", encoding="utf-8")
    (root / "nested" / "b.txt").write_text("x", encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"multi-gigabyte stand-in")

    rows = gm.hash_tree(root)
    assert [Path(row["path"]).name for row in rows] == ["a.json", "b.txt"]
    assert [row["hash_mode"] for row in rows] == ["full", "full"]

    absent = sandbox / "no-such-tree"
    assert gm.hash_tree(absent) == [{"path": str(absent), "exists": False}]


@pytest.mark.xfail(
    reason="denylist bug: verify() recomputes base_model/adapters/memory/verifier/evaluator and the "
           "prompts/tool_schema/datasets groups but never the `training` group, so a tampered training "
           "artifact still reports verified=True (eval/generation_manifest.py:153-157)",
    strict=False)
def test_verify_covers_every_hashed_artifact_including_the_training_group(sandbox):
    """The docstring: every artifact a manifest names carries a content hash and `verify()` recomputes them.

    A `training` entry produced by `hash_artifact` carries a hash like any other artifact. Tampering with
    the checkpoint it names is exactly the silent-reuse failure the module exists to prevent.
    """
    out = sandbox / "manifests"
    checkpoint = sandbox / "train.safetensors"
    checkpoint.write_bytes(b"checkpoint-v1")
    gm.freeze(out, _generation("gen-train", training={"checkpoint": gm.hash_artifact(checkpoint)}))
    assert gm.verify(out, "gen-train")["verified"] is True

    checkpoint.write_bytes(b"checkpoint-v2")

    result = gm.verify(out, "gen-train")
    assert any(str(checkpoint) in problem for problem in result["problems"])


# --------------------------------------------------------------------------- #
# BUDGET LEDGER
# --------------------------------------------------------------------------- #

def test_reserving_over_a_cap_raises_and_records_nothing(sandbox):
    """A stage that cannot reserve does not start, and a refused reservation leaves no trace."""
    path = sandbox / "ledger.jsonl"
    caps = bl.Caps(model_calls=4, compiles=10, seconds=600.0)
    ledger = bl.Ledger.open(path, caps)

    with pytest.raises(bl.BudgetExceeded) as excinfo:
        ledger.reserve("research-round", compiles=11)
    assert "compiles" in str(excinfo.value)
    assert "research-round" not in ledger.reservations
    assert ledger.spent == {}
    assert not path.exists()                             # no event was appended either

    # exactly the cap is allowed: the ceiling is a ceiling, not a margin
    assert ledger.reserve("research-round", compiles=10)["reused"] is False
    assert ledger.reservations["research-round"] == {"compiles": 10}
    assert ledger.spent == {}                            # a reservation is not a charge

    # what is already SPENT reduces what a later stage may ask for
    ledger.spend("research-round", compiles=8)
    with pytest.raises(bl.BudgetExceeded):
        ledger.reserve("second-round", compiles=3)
    assert "second-round" not in ledger.reservations

    # an unknown kind is a caller error, not a silent no-op
    with pytest.raises(ValueError):
        ledger.reserve("third-round", bananas=1)


def test_re_reserving_a_stage_after_reopen_returns_the_previous_reservation(sandbox):
    """The resume-idempotence guarantee: re-entering a stage must not double-charge it."""
    path = sandbox / "ledger.jsonl"
    caps = bl.Caps(model_calls=12, compiles=20, seconds=600.0)
    ledger = bl.Ledger.open(path, caps)

    first = ledger.reserve("training-run", compiles=6, model_calls=2)
    assert first == {"stage": "training-run", "amounts": {"compiles": 6, "model_calls": 2},
                     "reused": False}
    events_after_first = path.read_text("utf-8").splitlines()

    resumed = bl.Ledger.open(path, caps)
    again = resumed.reserve("training-run", compiles=6, model_calls=2)
    assert again["reused"] is True
    assert again["amounts"] == first["amounts"]
    assert path.read_text("utf-8").splitlines() == events_after_first

    # a resumed stage that asks for something else still gets the ORIGINAL reservation, and the cap is
    # not consulted: the charge already happened, so this is a lookup rather than a new decision
    greedy = resumed.reserve("training-run", compiles=999)
    assert greedy["reused"] is True
    assert greedy["amounts"] == first["amounts"]
    assert path.read_text("utf-8").splitlines() == events_after_first


def test_spend_accumulates_across_reopen_and_remaining_reflects_it(sandbox):
    path = sandbox / "ledger.jsonl"
    caps = bl.Caps(model_calls=12, compiles=72, seconds=1800.0)

    ledger = bl.Ledger.open(path, caps)
    ledger.spend("round-1", compiles=5, model_calls=3)
    assert ledger.remaining()["compiles"] == 67
    assert ledger.remaining()["model_calls"] == 9

    reopened = bl.Ledger.open(path, caps)
    assert reopened.spent == {"compiles": 5, "model_calls": 3}
    reopened.spend("round-2", compiles=7)
    assert reopened.spent == {"compiles": 12, "model_calls": 3}
    assert reopened.remaining()["compiles"] == 60
    assert reopened.remaining()["model_calls"] == 9

    # a second reopen sees the SUM, not the last report
    third = bl.Ledger.open(path, caps)
    assert third.spent == {"compiles": 12, "model_calls": 3}
    assert third.remaining()["compiles"] == 60

    # kinds nobody declared a ceiling for are absent from the report rather than reported as unlimited
    assert "train_steps" not in third.remaining()
    assert "tokens" not in third.remaining()


def test_guard_seconds_raises_once_the_wall_clock_cap_has_passed(sandbox):
    """Set `started_at` into the past instead of sleeping: same condition, no wall-clock cost."""
    caps = bl.Caps(seconds=5.0)
    fresh = bl.Ledger(path=sandbox / "fresh.jsonl", caps=caps)
    fresh.guard_seconds()                                # inside the cap: nothing to report

    expired = bl.Ledger(path=sandbox / "expired.jsonl", caps=caps, started_at=time.time() - 6.0)
    with pytest.raises(bl.BudgetExceeded) as excinfo:
        expired.guard_seconds()
    assert "wall clock" in str(excinfo.value)


def test_a_resumed_ledger_keeps_the_original_wall_clock_and_replays_from_disk(sandbox):
    """The ledger replays what is on disk, including the FIRST event's timestamp.

    A resumed run that starts a fresh wall clock has silently been given a second full budget, which is
    precisely how a two-generation experiment costs more than it declared.
    """
    path = sandbox / "ledger.jsonl"
    path.write_text(
        json.dumps({"type": "reserve", "stage": "research-round", "amounts": {"compiles": 4},
                    "at": 1000.0, "schema_version": bl.SCHEMA_VERSION}) + "\n" +
        json.dumps({"type": "spend", "stage": "research-round", "amounts": {"compiles": 3},
                    "at": 1001.0, "schema_version": bl.SCHEMA_VERSION}) + "\n",
        encoding="utf-8")

    resumed = bl.Ledger.open(path, bl.Caps(compiles=40, seconds=60.0))

    assert resumed.started_at == 1000.0
    assert resumed.reservations == {"research-round": {"compiles": 4}}
    assert resumed.spent == {"compiles": 3}
    assert [event["type"] for event in resumed.events] == ["reserve", "spend"]
    assert resumed.remaining()["compiles"] == 37
    assert resumed.remaining()["seconds_wall"] < 0        # the depleted clock is visible, not reset
    with pytest.raises(bl.BudgetExceeded):
        resumed.guard_seconds()
    # the held reservation is still held, so this stage cannot be re-entered for a fresh charge
    assert resumed.reserve("research-round", compiles=4)["reused"] is True


def test_reopening_replays_reservations_spends_and_releases(sandbox):
    path = sandbox / "ledger.jsonl"
    caps = bl.Caps(model_calls=12, compiles=40, seconds=1800.0)

    ledger = bl.Ledger.open(path, caps)
    ledger.reserve("research-round", compiles=8)
    ledger.reserve("evaluation", compiles=6)
    ledger.spend("research-round", compiles=5)
    ledger.release("evaluation")

    reopened = bl.Ledger.open(path, caps)
    assert reopened.reservations == {"research-round": {"compiles": 8}}
    assert reopened.spent == {"compiles": 5}
    assert [event["type"] for event in reopened.events] == ["reserve", "reserve", "spend", "release"]
    assert reopened.snapshot()["events"] == 4

    # a released stage is genuinely free again rather than silently still held
    assert reopened.reserve("evaluation", compiles=6)["reused"] is False


@pytest.mark.xfail(
    reason="eval/budget_ledger.py:15 states that `reserve_evaluation()` must succeed before research "
           "begins, but the module defines no such method (and no other module defines it), so nothing "
           "enforces the evaluation-first ordering the docstring promises",
    strict=False)
def test_the_documented_evaluation_first_entry_point_exists():
    assert hasattr(bl.Ledger, "reserve_evaluation")


# --------------------------------------------------------------------------- #
# DEMAND QUEUE
# --------------------------------------------------------------------------- #

def test_normalize_error_collapses_diagnostics_that_differ_only_in_file_and_line():
    """`cfe: Error: candidate.c, line 4: Syntax Error` and its line-12 sibling are ONE feature."""
    line4 = "cfe: Error: candidate.c, line 4: Syntax Error"
    line12 = "cfe: Error: candidate.c, line 12: Syntax Error"

    feature = rd.normalize_error(line4)
    assert feature == rd.normalize_error(line12)
    assert feature == "cfe: Error: Syntax Error"
    assert "candidate.c" not in feature
    assert not any(character.isdigit() for character in feature)

    # only the FIRST diagnostic is the feature, so a longer stderr cannot split one failure in two
    multi = line4 + "\ncfe: Error: candidate.c, line 9: Undefined symbol guMtxIdent"
    assert rd.normalize_error(multi) == feature

    # different failures stay different: a normaliser that collapsed everything would be useless
    assert rd.normalize_error(line4) != rd.normalize_error(
        "cfe: Error: candidate.c, line 4: Undefined symbol guMtxIdent")

    # a missing diagnostic is a stable feature of its own rather than a crash or an empty string
    assert rd.normalize_error("") == "no-error-recorded"
    assert rd.normalize_error(None) == "no-error-recorded"
    assert rd.normalize_error("   \n\t ") == "no-error-recorded"


def test_residual_kind_separates_register_allocation_from_structure():
    """register-only is a different problem from structural: it is allocation, not the C's shape."""
    assert rd.residual_kind("") == "no-diff"
    assert rd.residual_kind(None) == "no-diff"
    assert rd.residual_kind("  \n\t\n") == "no-diff"
    # a unified-diff header is not residual content
    assert rd.residual_kind("--- a/candidate.s\n+++ b/target.s\n") == "no-diff"

    register_only = "-lw v0,0(a0)\n+lw t6,0(a0)\n-sll v1,v0,2\n+sll a1,t6,2"
    assert rd.residual_kind(register_only) == "registers-only"

    # same opcode, different offset: the field is in the wrong place, which is structure
    assert rd.residual_kind("-lw v0,0(a0)\n+lw v0,0x24(a0)") == "structural"
    # different opcode: not an allocation difference
    assert rd.residual_kind("-lw v0,0(a0)\n+sw t6,0(a0)") == "structural"
    # one structural line is enough to make the whole residual structural
    assert rd.residual_kind(register_only + "\n-lw v0,0(a0)\n+nop") == "structural"


def test_size_bucket_boundaries():
    """Buckets keep one giant function from dominating a cluster, so the edges are the contract."""
    assert [rd.size_bucket(size) for size in (None, 0, 32)] == ["tiny", "tiny", "tiny"]
    assert [rd.size_bucket(size) for size in (33, 96)] == ["small", "small"]
    assert [rd.size_bucket(size) for size in (97, 256)] == ["medium", "medium"]
    assert rd.size_bucket(257) == "large"


def test_assert_no_held_out_fires_on_an_exact_name_and_ignores_a_longer_name():
    """Both directions matter. The guard was nearly deleted once for crying wolf on `guMtxIdentF`."""
    held_out = {"guMtxIdent", "__osPopThread"}

    # a longer, different function name must NOT trip the guard, in the structured fields or in the text
    longer_names_only = {"clusters": [{
        "cluster_id": "00000001", "error_class": "compiled", "residual_kind": "no-diff",
        "functions": ["guMtxIdentF", "__osPopThreadMain", "helper_guMtxIdent_2"],
        "examples": [{"function": "guMtxIdentF"}, {"function": "__osPopThreadMain"}]}]}
    rd.assert_no_held_out(longer_names_only, held_out)

    # an exact held-out name in the function list IS a leak
    leaked_as_function = {"clusters": [{
        "cluster_id": "00000002", "functions": ["guMtxIdentF", "guMtxIdent"],
        "examples": [{"function": "guMtxIdentF"}]}]}
    with pytest.raises(AssertionError) as excinfo:
        rd.assert_no_held_out(leaked_as_function, held_out)
    assert "guMtxIdent" in str(excinfo.value)

    # so is an exact held-out name in an example row, even when it never reached the function list
    leaked_as_example = {"clusters": [{
        "cluster_id": "00000003", "functions": ["guMtxIdentF"],
        "examples": [{"function": "__osPopThread"}]}]}
    with pytest.raises(AssertionError) as excinfo:
        rd.assert_no_held_out(leaked_as_example, held_out)
    assert "__osPopThread" in str(excinfo.value)

    # and the text scan catches a whole word anywhere else in the written artifact
    leaked_in_text = {"clusters": [{
        "cluster_id": "00000004", "functions": ["guMtxIdentF"],
        "error_class": "Undefined symbol guMtxIdent"}]}
    with pytest.raises(AssertionError):
        rd.assert_no_held_out(leaked_in_text, held_out)


_DEMAND_SCHEMA = """
create table functions (name text, addr integer, size integer);
create table attempts (func_addr integer, exact integer, score real, compiled integer,
                       compiler_stderr text, diff_summary text, strategy text,
                       id integer primary key);
"""

# Only the columns the module reads: functions(name, addr, size) and attempts(func_addr, exact, score,
# compiled, compiler_stderr, diff_summary, strategy).
_DEMAND_FUNCTIONS = [
    ("f_alpha", 0x80100000, 20),        # tiny, compiled, register-only residual
    ("f_beta", 0x80100010, 24),         # tiny, compiled, the same residual -> same feature triple
    ("f_gamma", 0x80100020, 100),       # medium, compiled, structural residual
    ("f_heldout", 0x80100030, 20),      # held out: must never be clustered
    ("f_delta", 0x80100040, 40),        # small, did not compile, diagnostic at line 4
    ("f_epsilon", 0x80100050, 44),      # small, did not compile, same diagnostic at line 12
    ("f_unattempted", 0x80100060, 30),  # no logged attempt: nothing to cluster it from
]

_DEMAND_ATTEMPTS = [
    (0x80100000, 0, 10.0, 1, "", "-lw v0,0(a0)\n+lw t6,0(a0)", "seed"),
    (0x80100010, 0, 10.0, 1, "", "-lw v0,0(a0)\n+lw t6,0(a0)", "seed"),
    (0x80100020, 0, 5.0, 1, "", "-lw v0,0(a0)\n+lw v0,0x24(a0)", "seed"),
    (0x80100030, 0, 10.0, 1, "", None, "seed"),
    (0x80100040, 0, 0.0, 0, "cfe: Error: candidate.c, line 4: Syntax Error", None, "seed"),
    (0x80100050, 0, 0.0, 0, "cfe: Error: candidate.c, line 12: Syntax Error", None, "seed"),
]


def _demand_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DEMAND_SCHEMA)
    conn.executemany("insert into functions values (?, ?, ?)", _DEMAND_FUNCTIONS)
    conn.executemany("insert into attempts (func_addr, exact, score, compiled, compiler_stderr,"
                     " diff_summary, strategy) values (?, ?, ?, ?, ?, ?, ?)", _DEMAND_ATTEMPTS)
    return conn


def test_clustering_makes_one_cluster_per_feature_triple_and_excludes_held_out():
    conn = _demand_conn()

    payload = rd.build_clusters(conn, held_out={"f_heldout"})

    clusters = payload["clusters"]
    by_triple = {(cluster["error_class"], cluster["residual_kind"], cluster["size_bucket"]): cluster
                 for cluster in clusters}
    assert set(by_triple) == {("compiled", "registers-only", "tiny"),
                              ("compiled", "structural", "medium"),
                              ("cfe: Error: Syntax Error", "no-diff", "small")}
    assert len(clusters) == 3                                  # one cluster per triple, not one per attempt
    assert [cluster["attempts"] for cluster in clusters] == sorted(
        (cluster["attempts"] for cluster in clusters), reverse=True)   # most frequent first

    assert by_triple[("compiled", "registers-only", "tiny")]["functions"] == ["f_alpha", "f_beta"]
    assert by_triple[("compiled", "structural", "medium")]["functions"] == ["f_gamma"]
    # the line numbers were masked before the key was built, so two functions share one cluster
    assert by_triple[("cfe: Error: Syntax Error", "no-diff", "small")]["functions"] == [
        "f_delta", "f_epsilon"]

    clustered = {name for cluster in clusters for name in cluster["functions"]}
    assert "f_heldout" not in clustered
    assert "f_unattempted" not in clustered                    # eligible means "has a logged attempt"
    assert payload["eligible_functions"] == 5
    assert payload["held_out_excluded"] == 1
    assert payload["schema_version"] == rd.SCHEMA_VERSION

    # the queue's own guard agrees with the filter that built it
    rd.assert_no_held_out(payload, {"f_heldout"})


def test_a_function_contributes_exactly_one_member_and_counts_all_its_attempts():
    """The corrected invariant. The first version appended one member per stored ATTEMPT (each built by
    re-querying that function's best attempt), so four attempts produced four identical members and the
    cluster reported four attempts for ONE function, inflating the frequency signal the scheduler sorts
    on. One member per function, with the real attempt count summed."""
    conn = _demand_conn()
    conn.executemany("insert into attempts (func_addr, exact, score, compiled, compiler_stderr,"
                     " diff_summary, strategy) values (?, ?, ?, ?, ?, ?, ?)",
                     [(0x80100000, 0, 11.0 + index, 1, "", "-lw v0,0(a0)\n+lw t6,0(a0)", "seed")
                      for index in range(4)])

    payload = rd.build_clusters(conn, held_out=set())
    cluster = next(c for c in payload["clusters"]
                   if (c["error_class"], c["residual_kind"]) == ("compiled", "registers-only"))

    assert cluster["functions"] == ["f_alpha", "f_beta"]       # listed once each, however many attempts
    alpha = next(m for m in cluster["examples"] if m["function"] == "f_alpha")
    assert alpha["attempts"] == 5                              # 1 from the fixture + 4 inserted
    assert cluster["attempts"] == alpha["attempts"] + 1        # f_beta's single attempt


def test_cluster_ids_are_stable_across_processes():
    """The queue is written to disk and cited by cluster id, so `hash()` (salted per process) made the
    artifact unreproducible: the same cluster was named differently on every run."""
    payload = rd.build_clusters(_demand_conn(), held_out=set())
    first = {c["cluster_id"]: (c["error_class"], c["residual_kind"], c["size_bucket"])
             for c in payload["clusters"]}
    second = {c["cluster_id"]: (c["error_class"], c["residual_kind"], c["size_bucket"])
              for c in rd.build_clusters(_demand_conn(), held_out=set())["clusters"]}
    assert first == second
    assert all(len(cid) == 12 for cid in first)


def test_held_out_names_takes_the_test_split_and_every_panel_row(sandbox):
    """The filter's key names are the guard: a wrong key returns the empty set and silently disables it."""
    splits = sandbox / "splits.json"
    splits.write_text(json.dumps({"train": ["train_fn"], "dev": ["dev_fn"],
                                  "test": ["test_fn", "test_fn_2"]}), encoding="utf-8")
    panel = sandbox / "panel.json"
    panel.write_text(json.dumps({"rows": [{"function": "panel_fn"}, {"function": "panel_fn_2"},
                                          {"score": 1.0}]}), encoding="utf-8")

    names = rd.held_out_names(splits, [panel, sandbox / "absent.json"])

    assert names == {"test_fn", "test_fn_2", "panel_fn", "panel_fn_2"}
    assert "train_fn" not in names and "dev_fn" not in names   # train/dev are the clusterable splits


def test_held_out_names_matches_the_real_splits_and_panel_files():
    """Same check against production data, because a fixture cannot catch a key-name disagreement.

    `tests/conftest.py` records this exact bug class landing once already: a split file keyed on
    `function` was read with `entry["name"]`, so the one guard that must never be wrong returned nothing
    and looked healthy. Skips (rather than fails) where the results tree is absent.
    """
    splits = REPO_ROOT / "eval/results/tool-action-20260921/splits.json"
    panels = [REPO_ROOT / "eval/results/tool-agent-20260920/head-to-head.json",
              REPO_ROOT / "eval/results/tool-action-20260921/eval-adapter-panel-dev.json"]
    if not splits.exists():
        pytest.skip(f"results tree not present: {splits}")

    payload = json.loads(splits.read_text("utf-8"))
    test_split = set(payload.get("test") or [])
    assert test_split, "the real splits file has no test split, so nothing would be held out"

    names = rd.held_out_names(splits, [path for path in panels if path.exists()])

    assert test_split <= names
    for panel in panels:
        if panel.exists():
            rows = json.loads(panel.read_text("utf-8")).get("rows") or []
            assert {row["function"] for row in rows if row.get("function")} <= names


_CLUSTER_ID_PROBE = """
import json, sqlite3
from eval import research_demand as rd

conn = sqlite3.connect(":memory:")
conn.executescript(
    "create table functions (name text, addr integer, size integer);"
    "create table attempts (func_addr integer, exact integer, score real, compiled integer,"
    " compiler_stderr text, diff_summary text, strategy text);")
conn.execute("insert into functions values ('probe_fn', 1, 15)")
conn.execute("insert into attempts values (1, 0, 0.0, 1, '', null, 'seed')")
print(json.dumps([c["cluster_id"] for c in rd.build_clusters(conn, held_out=set())["clusters"]]))
"""


def _cluster_ids_in_fresh_process(seed: str) -> list[str]:
    environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO_ROOT)}
    completed = subprocess.run([sys.executable, "-c", _CLUSTER_ID_PROBE],
                               capture_output=True, text=True, env=environment, timeout=120)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.xfail(
    reason="cluster_id is abs(hash(feature_tuple)) % 10**8 (eval/research_demand.py:105); str hashing is "
           "salted per process, so the same queue written twice gets different ids for the same cluster",
    strict=False)
def test_cluster_ids_are_identical_in_two_fresh_processes():
    """The queue is written to disk and cited, so its ids have to be reproducible, not process-local."""
    try:
        first = _cluster_ids_in_fresh_process("1")
        second = _cluster_ids_in_fresh_process("2")
    except OSError as exc:                                   # no subprocess capture available here
        pytest.skip(f"cannot spawn a python subprocess: {exc}")

    assert first and first == second

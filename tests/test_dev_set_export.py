"""The development-set export: exact bytes from provenance, or an explicit failure.

THE RULE THIS PINS. A dev set drawn from a measurement must contain the bytes that were measured. The
tempting alternative -- re-derive the candidate by replaying the action sequence -- produces something
that looks equivalent and is not, and the substitution is invisible afterwards. So every row is recovered
by the sha256 the probe recorded, a mismatch is an error rather than a rebuild, and an unrecoverable row
fails the export instead of shrinking the set.

No compiler and no repo are needed here: the selection, the recovery and the tiering are pure functions of
a frame, a KB and a step list. The fresh-compile path is exercised by the WSL export, not by this file.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import dev_set_export as export                                # noqa: E402


def _kb(path: Path, rows: list[tuple[str, str]]) -> Path:
    """A minimal attempt log: (source, iteration) pairs, hashed the way the real log hashes them."""
    conn = sqlite3.connect(path)
    conn.execute("create table attempts (source_code text, source_sha256 text, iteration integer,"
                 " run_id text)")
    for source, iteration in rows:
        conn.execute("insert into attempts values (?,?,?,?)",
                     (source, export.sha256_text(source), iteration, "test-run"))
    conn.commit()
    conn.close()
    return path


def _row(name: str, source: str, *, compiled=True, frontend=True, exact=False, steps=()):
    actions = {f"eval.intake_runners.{step}": {"changed": True, "status": "ok"} for step in steps}
    return {"function": name, "size": 40, "tier": "small", "failure_class": "syntax-error",
            "draft_sha256": export.sha256_text(f"draft-{name}"),
            "actions": actions,
            "sequence": {"compiled": compiled, "exact": exact, "score": 91.0,
                         "frontend": "passed" if frontend else "rejected",
                         "frontend_passed": frontend,
                         "frontend_classes": [] if frontend else ["undeclared-identifier"],
                         "final_sha256": export.sha256_text(source),
                         "final_source_chars": len(source)}}


FRAME = {"schema_version": 5, "seconds": 12.0,
         "rows": [_row("finishable_a", "int a(void){return 1;}\n", steps=("resolve_placeholders",)),
                  _row("finishable_b", "int b(void){return 2;}\n",
                       steps=("resolve_placeholders", "header_variant")),
                  _row("finishable_c", "int c(void){return 3;}\n",
                       steps=("source_type_declarations", "resolve_placeholders")),
                  _row("already_exact", "int d(void){return 4;}\n", exact=True),
                  _row("not_compiling", "int e(void){return 5;}\n", compiled=False, frontend=False),
                  _row("frontend_rejected", "int f(void){return 6;}\n", frontend=False),
                  _row("sealed_task", "int g(void){return 7;}\n")]}


def test_selection_keeps_the_frontend_passing_nonexact_rows_and_names_every_exclusion():
    plan = export.plan(FRAME, sealed={"sealed_task"})
    assert [row["function"] for row in plan["selected"]] == [
        "finishable_a", "finishable_b", "finishable_c"]
    reasons = {row["function"]: row["reason"] for row in plan["excluded"]}
    assert reasons["already_exact"].startswith("already byte-exact")
    assert reasons["not_compiling"].startswith("does not compile")
    assert reasons["frontend_rejected"].startswith("compiles but")
    assert reasons["sealed_task"] == "in the sealed test split"


def test_a_sealed_task_never_enters_the_development_set():
    """The sealed split is the only basis for reported numbers. A dev set that quietly contains it is
    the contamination this project's evaluation story rests on avoiding."""
    plan = export.plan(FRAME, sealed={"sealed_task", "finishable_a", "finishable_b", "finishable_c"})
    assert plan["selected"] == []
    # The sealed reason is checked FIRST, so a row that would otherwise qualify is excluded for the only
    # reason that matters.
    sealed_excluded = {row["function"] for row in plan["excluded"]
                       if row["reason"] == "in the sealed test split"}
    assert sealed_excluded == {"sealed_task", "finishable_a", "finishable_b", "finishable_c"}


def test_the_exact_bytes_behind_the_hash_are_recovered(tmp_path):
    kb = _kb(tmp_path / "kb.sqlite", [("int a(void){return 1;}\n", 3)])
    conn = sqlite3.connect(kb)
    try:
        source, provenance = export.recover_source(conn, export.sha256_text("int a(void){return 1;}\n"))
    finally:
        conn.close()
    assert source == "int a(void){return 1;}\n" and provenance["attempt_iteration"] == 3


def test_a_row_whose_bytes_cannot_be_produced_is_unrecoverable_not_rebuilt(tmp_path):
    """Three ways to fail, and none of them is allowed to fall back to a fresh draft."""
    kb = _kb(tmp_path / "kb.sqlite", [("int a(void){return 1;}\n", 1)])
    conn = sqlite3.connect(kb)
    try:
        # (1) no row carries the hash at all
        source, why = export.recover_source(conn, export.sha256_text("something else"))
        assert source is None and "no attempt row carries" in why["reason"]
        # (2) a row carries the hash but its bytes hash to something else -- a corrupted log
        conn.execute("update attempts set source_code = ?", ("tampered",))
        conn.commit()
        source, why = export.recover_source(conn, export.sha256_text("int a(void){return 1;}\n"))
        assert source is None and "none of their bytes hash to it" in why["reason"]
    finally:
        conn.close()
    # (3) the frame row records no hash at all
    conn = sqlite3.connect(kb)
    try:
        source, why = export.recover_source(conn, "")
    finally:
        conn.close()
    assert source is None and "records no final candidate hash" in why["reason"]


def test_an_unrecoverable_row_fails_the_export_and_is_named(tmp_path, capsys):
    kb = _kb(tmp_path / "kb.sqlite", [("int a(void){return 1;}\n", 1)])
    frame = tmp_path / "frame.json"
    frame.write_text(json.dumps(FRAME), encoding="utf-8")
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"test": ["sealed_task"]}), encoding="utf-8")
    out = tmp_path / "dev-set.json"
    code = export.main(["--frame", str(frame), "--kb", str(kb), "--splits", str(splits),
                        "--out", str(out), "--sources", str(tmp_path / "sources"),
                        "--no-fresh-compile"])
    assert code == 1, "a set that lost rows must not exit zero"
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["selection"]["selected"] == 1          # only `finishable_a` is in the log
    assert payload["selection"]["unrecoverable"] == 2, "the two rows with no attempt are named"
    assert {row["function"] for row in payload["selection"]["unrecoverable_detail"]} == {
        "finishable_b", "finishable_c"}
    assert payload["selection"]["unrecoverable_detail"][0]["recovery"]["reason"]
    # AND THE RECOVERABLE PART IS STILL WRITTEN, with the shortfall in the artifact rather than only in
    # the exit status: a caller that ignores the code still cannot read the set as complete.
    assert [entry["function"] for entry in payload["entries"]] == ["finishable_a"]


def test_a_complete_export_carries_source_identity_assistance_and_exclusion(tmp_path):
    sources = ["int a(void){return 1;}\n", "int b(void){return 2;}\n", "int c(void){return 3;}\n"]
    kb = _kb(tmp_path / "kb.sqlite", [(s, i) for i, s in enumerate(sources, 1)])
    frame = tmp_path / "frame.json"
    frame.write_text(json.dumps(FRAME), encoding="utf-8")
    splits = tmp_path / "splits.json"
    splits.write_text(json.dumps({"test": ["sealed_task"]}), encoding="utf-8")
    out, source_dir = tmp_path / "dev-set.json", tmp_path / "sources"
    code = export.main(["--frame", str(frame), "--kb", str(kb), "--splits", str(splits),
                        "--out", str(out), "--sources", str(source_dir), "--no-fresh-compile"])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["selection"] == {**payload["selection"], "selected": 3, "excluded": 4,
                                    "unrecoverable": 0}
    by_name = {entry["function"]: entry for entry in payload["entries"]}
    assert set(by_name) == {"finishable_a", "finishable_b", "finishable_c"}
    for entry in by_name.values():
        # the bytes on disk are the bytes the hash names, or the entry is a lie
        path = source_dir / f"{entry['function']}.c"
        assert export.sha256_text(path.read_text(encoding="utf-8")) == entry["sha256"]
        assert entry["training_eligible"] is False
        assert entry["training_exclusion"].startswith("development panel")
        assert entry["lineage"]["baseline_draft_sha256"]
        assert entry["lineage"]["steps_that_changed"]
        assert entry["recovered_from"]["attempt_run_id"] == "test-run"
        assert entry["fresh"]["status"] == "not-compiled", "the export must not claim a fresh result"


def test_assistance_tiers_follow_the_steps_that_produced_the_candidate():
    assert export.assistance_tier(["resolve_placeholders"])["tier"] == "binary-only"
    header = export.assistance_tier(["resolve_placeholders", "header_variant"])
    assert header["tier"] == "header-assisted" and header["header_steps"] == ["header_variant"]
    reference = export.assistance_tier(["header_variant", "source_type_declarations"])
    assert reference["tier"] == "reference-source-assisted"
    assert reference["reference_source_steps"] == ["source_type_declarations"]


def test_a_reference_source_step_is_never_reported_as_binary_only():
    """The tier is the claim. A candidate whose winning sequence needed a declaration recovered from
    the reference decomp's `src/` is not binary-only evidence, whatever its score."""
    for steps in (["source_type_declarations"], ["source_type_declarations", "header_variant"],
                  ["or_address", "source_type_declarations"]):
        assert export.assistance_tier(steps)["tier"] != "binary-only"


def test_the_exported_frame_is_marked_development_only():
    """The regime string is part of the artifact: a reader must not have to infer it from the path."""
    assert "DEVELOPMENT DATA" in export.REGIME
    assert "Never a sealed test set" in export.REGIME

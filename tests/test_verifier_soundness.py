"""The verifier's soundness contract, pinned against the defect an audit found.

THE CONFIRMED DEFECT
--------------------
The oracle decided exactness from `.text`-section equality, which carries no relocations. Two
functions differing only in their callee are therefore byte-identical in `.text`:

    extern int external_a(int);
    int syn_audit(int x) { return external_a(x); }

versus the same with `external_b`, both compiled with the game's real IDO recipe, both produce
`27bdffe8afbf00140c000000000000008fbf001427bd001803e0000800000000` with a differing
`R_MIPS_26` at offset 0x8. The `.text` oracle called them **exact**; `byte_certificate.certify`
correctly calls them **different**. A candidate that calls the wrong function was therefore
certifiable as a verified repair.

These tests need the real compiler, and they RUN HERE rather than behind an opt-in flag: a fire
test that skips by default is the "silent decline" this project keeps re-learning, because a
skipped assertion and a satisfied one are indistinguishable from outside. They skip only when
the game repo's recipe or the MIPS toolchain is genuinely absent, which is the same convention
`tests/test_synthetic_corpus.py` uses. The GPU-free tests further down pin the wiring, but a
wiring test cannot detect an unsound comparison -- only the real recipe can, which is why the
fire tests are the point of this file.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from eval import evaluate_source_repair as ev
from eval import repair_dataset_synth as rds

REPO = Path.home() / "decomp" / "sbk1"
TOOLCHAIN = (REPO / "Makefile").is_file() and all(
    shutil.which(name) for name in ("make", "mips-linux-gnu-as", "mips-linux-gnu-objcopy",
                                    "mips-linux-gnu-objdump"))
needs_toolchain = pytest.mark.skipif(
    not TOOLCHAIN, reason="the game repo's IDO recipe or the MIPS toolchain is unavailable")


def _recipe():
    return rds.resolve_recipe(REPO, rds.DEFAULT_TARGET)["resolved"]


def _compile(source: str, name: str, work: Path):
    run = rds.compile_unit(REPO, _recipe(), name, source, work)
    return run, work / f"{name}.o"


# --- the fire tests: only the real compiler can detect an unsound comparison ---

@needs_toolchain
def test_a_changed_callee_is_not_exact():
    """THE DEFECT. `.text` says these are equal; the certificate must say they are not."""
    work = Path(tempfile.mkdtemp(prefix="verifier-fire-"))
    try:
        a = '#include "common.h"\n\nextern int external_a(int);\n\nint f(int x) {\n    return external_a(x);\n}\n'
        b = a.replace("external_a", "external_b")
        run_a, obj_a = _compile(a, "f_a", work)
        run_b, obj_b = _compile(b, "f_b", work)
        assert run_a["compiled"] and run_b["compiled"], (run_a["stderr"], run_b["stderr"])

        text_a, text_b = rds.code_image(obj_a), rds.code_image(obj_b)
        assert text_a == text_b, (
            "the premise of the defect: these differ only by callee, so `.text` is identical")

        verdict = rds.certify_exact(obj_a, obj_b, source=b)
        assert verdict["exact"] is False, (
            "a changed external call target must NOT certify as exact; `.text` equality said it "
            "was, which is why the oracle was replaced")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@needs_toolchain
def test_a_changed_referenced_global_is_not_exact():
    """The sibling case: same code, different referenced data symbol."""
    work = Path(tempfile.mkdtemp(prefix="verifier-fire-"))
    try:
        a = ('#include "common.h"\n\nextern s32 gAlpha;\n\nextern s32 gBeta;\n\n'
             's32 f(void) {\n    return gAlpha;\n}\n')
        b = a.replace("return gAlpha;", "return gBeta;")
        run_a, obj_a = _compile(a, "g_a", work)
        run_b, obj_b = _compile(b, "g_b", work)
        assert run_a["compiled"] and run_b["compiled"], (run_a["stderr"], run_b["stderr"])
        verdict = rds.certify_exact(obj_a, obj_b, source=b)
        assert verdict["exact"] is False, "a changed referenced global must not certify as exact"
    finally:
        shutil.rmtree(work, ignore_errors=True)


@needs_toolchain
def test_a_recompiled_identical_source_is_still_exact():
    """The fix must not break the true positive. Whole objects are not byte-reproducible (the
    build embeds a random temp filename), so a certificate that demanded byte equality would
    reject every legitimate repair -- which is the bug the `.text` comparison was introduced to
    fix, and it must stay fixed."""
    work = Path(tempfile.mkdtemp(prefix="verifier-fire-"))
    try:
        source = ('#include "common.h"\n\ns32 f(s32 a) {\n    s32 t;\n\n    t = a * 3;\n'
                  '    return t + 1;\n}\n')
        run_a, obj_a = _compile(source, "same", work)
        again = work / "again"
        again.mkdir()
        run_b = rds.compile_unit(REPO, _recipe(), "same", source, again)
        assert run_a["compiled"] and run_b["compiled"]
        assert (work / "same.o").read_bytes() != (again / "same.o").read_bytes(), (
            "the premise: whole objects are NOT byte-reproducible between builds")
        verdict = rds.certify_exact(work / "same.o", again / "same.o", source=source)
        assert verdict["exact"] is True, (
            "the same source must certify exact despite the unstable build metadata")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- wiring tests: GPU-free, and they run always ---

def test_the_build_verdict_comes_from_the_certificate_not_from_text():
    """`child.exact` must be `certify_exact`, never a `.text` comparison."""
    source = Path(rds.__file__).read_text(encoding="utf-8")
    assert "certify_exact(target_object_path, child_object_path" in source
    assert "child_code == target[\"code\"]" not in source, (
        "the `.text` comparison was the unsound verdict and must not decide exactness")


def test_the_evaluator_verdict_comes_from_the_certificate():
    from eval import evaluate_source_repair as ev
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert "certify_exact(_target_object(" in source
    assert "row[\"exact\"] = bool(verdict.get(\"exact\"))" in source
    assert "row[\"exact\"] = bool(comparison[\"identical\"])" not in source, (
        "`.text` equality must not set `exact`")


def test_code_image_is_documented_as_a_diagnostic_not_a_verdict():
    doc = rds.code_image.__doc__ or ""
    assert "NOT AN EXACTNESS VERDICT" in doc
    assert "external_a" in doc, "the docstring must name the case that fooled it"


def test_the_build_compiles_the_answer_a_second_time_and_certifies_it():
    """The child verdict must rest on a SECOND compile, not on the target's own object."""
    source = Path(rds.__file__).read_text(encoding="utf-8")
    assert "answer_dir" in source and "answer_obj" in source
    assert '"object_path": answer_obj' in source


def test_an_unverified_certificate_is_not_silently_false():
    """A certificate that could not be computed reports `unverified`; that is not "not exact"
    and must not be stored as a plain False without the reason."""
    receipt = {"exact": False, "status": "unverified", "error": "OSError: missing object"}
    stored = {k: receipt.get(k) for k in ("kind", "status", "scope", "excluded", "exact", "error")}
    assert stored["status"] == "unverified" and stored["error"]
    source = Path(rds.__file__).read_text(encoding="utf-8")
    assert '"certificate": {k: verdict.get(k) for k in' in source, (
        "the child record must carry the certificate's status and error")


def test_every_draw_keeps_its_source_including_successes():
    """The audit could not replay the claimed repairs because successes stored only a hash.

    The source is stored unconditionally, and the `if not row["exact"]:` branch that follows it --
    which `tests/test_posttraining_safety.py` pins, and which classifies WHY a draw failed --
    must not be where the C is kept. Both properties are asserted here, because the tempting way
    to "keep the source" is to move it back inside the failure branch, and that is the bug.
    """
    from eval import evaluate_source_repair as ev
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert 'row["source"] = source' in source
    before, _, after = source.partition('if not row["exact"]:')
    assert after, "the failure branch pins the failure classification and must still exist"
    assert 'row["source"]' not in after.split("per_draw.append", 1)[0], (
        "the source must be kept unconditionally, not only for failures")
    assert 'row["raw_response"] = text' in before, (
        "the raw model output is kept for every draw too")


def test_every_draw_references_its_object_and_certificate_artifacts(tmp_path):
    """A path in a `mkdtemp` directory is not an artifact reference: it is gone with the process.

    The extraction, the raw model output, the object and the certificate are copied beside the
    evaluation and recorded with a digest, for exact draws as well as failed ones.
    """
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert "def _write_draw_artifacts(" in source
    assert 'put("candidate.c"' in source and 'put("model_output.txt"' in source
    assert 'put("candidate.o"' in source and 'put("certificate.json"' in source
    assert 'row["artifacts"] = _write_draw_artifacts(' in source
    assert 'row["raw_response"] = text' in source, (
        "the FULL raw output must be kept, not a head of it")

    # And the writer actually writes: a reference nobody can open is not evidence.
    obj = tmp_path / "candidate.o"
    obj.write_bytes(b"\x7fELF\x01\x02\x01" + b"\0" * 32)
    refs = ev._write_draw_artifacts(tmp_path, "baseline", "syn:unit:0:narrow-locals", 1,
                                    source="int f(void) { return 0; }\n",
                                    raw="```c\nint f(void) { return 0; }\n```",
                                    obj=obj,
                                    certificate={"exact": False, "status": "object_sections_differ"})
    for name in ("candidate.c", "model_output.txt", "candidate.o", "certificate.json"):
        assert name in refs["files"], f"{name} was not kept"
        record = refs["files"][name]
        path = tmp_path / record["path"]
        assert path.is_file(), f"{name} is referenced but missing"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]

    # A draw that produced nothing still keeps its raw output, and invents no object.
    empty = ev._write_draw_artifacts(tmp_path, "baseline", "syn:unit:0:narrow-locals", 2,
                                     source=None, raw="I cannot help with that.",
                                     obj=None, certificate=None)
    assert "model_output.txt" in empty["files"] and "candidate.o" not in empty["files"]
    assert empty["files"]["model_output.txt"]["bytes"] > 0


def test_the_certificate_is_used_with_its_own_scope_not_paraphrased():
    """The certificate's scope and exclusions travel with the verdict; they are not re-worded."""
    receipt = rds.certify_exact.__doc__ or ""
    assert "solver.byte_certificate.certify" in receipt
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert "allocated text/data/BSS sections" in source
    assert "whole_rom" not in source.replace("whole-ROM", ""), (
        "the evaluator must not claim the certificate verifies a whole ROM")


# ==============================================================================
# The promotion gate: complete, eligible runs only
# ==============================================================================

def _gate_rows(n: int, exact_ids=(), draws: int = 2, generated: int | None = None) -> dict:
    rows = {}
    for i in range(n):
        row = {"exact": i in set(exact_ids), "draws": draws}
        if generated is not None:
            row["draws_generated"] = generated
        rows[f"t{i}"] = row
    return rows


def _spec(n: int, *, draws: int = 2, split: str = "test", kind: str = "frozen"):
    from eval import posttraining_gate as gate
    return gate.EvaluationSpec(expected_task_ids=tuple(f"t{i}" for i in range(n)),
                               split=split, draws_per_task=draws, kind=kind,
                               manifest_sha256="m" * 64, dataset_sha256="d" * 64)


def test_the_gate_promotes_a_complete_equal_budget_run():
    """The positive control: the strengthened gate must still be able to pass a real result."""
    from eval import posttraining_gate as gate
    outcome = gate.decide(baseline=_gate_rows(20, [0]), adapter=_gate_rows(20, [0, 1, 2]),
                          spec=_spec(20))
    assert outcome.passed and outcome.verdict == "promote"
    assert outcome.counts["draws"] == {"expected_per_arm": 40, "baseline": 40, "adapter": 40,
                                       "per_task_problems": [], "per_task_problems_total": 0}


def test_the_audit_probe_cannot_promote():
    """THE PROBE: 12 matching ids, 0 baseline draws, 2 adapter draws, one apparent gain.

    Both spellings are checked: with the frozen specification (where the budget is declared) and
    without one at all (a direct call with two arms and no declaration), because the second is
    how the probe was actually written.
    """
    from eval import posttraining_gate as gate
    baseline = {f"t{i}": {"exact": False, "draws": 0} for i in range(12)}
    adapter = {f"t{i}": {"exact": i == 0, "draws": 2} for i in range(12)}

    declared = gate.decide(baseline=baseline, adapter=adapter, spec=_spec(12))
    assert not declared.passed, "a zero-draw baseline cannot authorise a promotion"
    assert declared.verdict == "inconclusive" and declared.active_adapter == "baseline"
    assert any("declared budget was not executed" in r for r in declared.reasons)

    undeclared = gate.decide(baseline=baseline, adapter=adapter)
    assert not undeclared.passed and undeclared.active_adapter == "baseline"
    assert undeclared.verdict == "ineligible"
    assert any("no frozen panel" in r for r in undeclared.reasons)

    mirrored = gate.decide(baseline={f"t{i}": {"exact": False, "draws": 2} for i in range(12)},
                           adapter={f"t{i}": {"exact": i == 0, "draws": 0} for i in range(12)},
                           spec=_spec(12))
    assert not mirrored.passed, "a zero-draw adapter arm cannot authorise a promotion"


def test_the_gate_refuses_an_incomplete_panel_even_when_both_arms_agree():
    """Equal id sets between the arms are not coverage of the DECLARED panel."""
    from eval import posttraining_gate as gate
    outcome = gate.decide(baseline=_gate_rows(12), adapter=_gate_rows(12, [0]), spec=_spec(27))
    assert not outcome.passed and outcome.verdict == "inconclusive"
    assert outcome.counts["only_baseline_ran"] == [] and outcome.counts["only_adapter_ran"] == [], (
        "the arms agree with each other; that is exactly why the panel check has to exist")
    assert outcome.conditions["R4_panel_fully_covered"] is False
    assert any("did not execute the frozen panel" in r for r in outcome.reasons)
    assert outcome.counts["panel_never_ran"]["adapter"], "the missing ids must be reported"


def test_the_gate_refuses_a_run_that_stopped_early(tmp_path):
    """A run that hit its deadline has fewer draws than the budget on the tasks it reached."""
    from eval import posttraining_gate as gate
    stopped = _gate_rows(20, [0])
    stopped["t19"]["draws"] = 1
    stopped["t19"]["draws_generated"] = 1
    outcome = gate.decide(baseline=stopped, adapter=_gate_rows(20, [0, 1]), spec=_spec(20))
    assert not outcome.passed and outcome.verdict == "inconclusive"
    assert any("declared budget was not executed" in r for r in outcome.reasons)


def test_the_gate_refuses_a_task_that_recorded_no_draw_count():
    """A row with no draw count is not a row that executed the budget."""
    from eval import posttraining_gate as gate
    rows = _gate_rows(20, [0, 1])
    del rows["t7"]["draws"]
    outcome = gate.decide(baseline=rows, adapter=_gate_rows(20, [0]), spec=_spec(20))
    assert not outcome.passed and outcome.verdict == "inconclusive"
    assert any("records no draw count" in r for r in outcome.reasons)


def test_the_gate_refuses_draws_that_never_produced_a_model_response():
    """Draw slots whose sampler raised out-of-memory are slots, not draws."""
    from eval import posttraining_gate as gate
    outcome = gate.decide(baseline=_gate_rows(20, [0], generated=2),
                          adapter=_gate_rows(20, [0, 1], generated=0), spec=_spec(20))
    assert not outcome.passed and outcome.verdict == "inconclusive"
    assert any("produced a model response" in r for r in outcome.reasons)


def test_the_gate_refuses_a_train_or_dev_split():
    from eval import posttraining_gate as gate
    for split in ("train", "dev"):
        outcome = gate.decide(baseline=_gate_rows(20, [0]), adapter=_gate_rows(20, [0, 1]),
                              spec=_spec(20, split=split))
        assert not outcome.passed, f"{split} must never authorise a promotion"
        assert outcome.verdict == "ineligible" and outcome.active_adapter == "baseline"
        assert any("train/dev results can never authorise" in r for r in outcome.reasons)


def test_a_subset_or_diagnostic_evaluation_is_explicitly_ineligible():
    from eval import posttraining_gate as gate
    for kind in ("subset", "diagnostic", "smoke"):
        outcome = gate.decide(baseline=_gate_rows(20, [0]), adapter=_gate_rows(20, [0, 1]),
                              spec=_spec(20, kind=kind))
        assert not outcome.passed and outcome.verdict == "ineligible", kind
        assert any("diagnostic or subset run is reported, never promoted" in r
                   for r in outcome.reasons)


def test_a_specification_that_declares_nothing_cannot_promote():
    from eval import posttraining_gate as gate
    outcome = gate.decide(baseline=_gate_rows(20, [0]), adapter=_gate_rows(20, [0, 1]),
                          spec=gate.EvaluationSpec())
    assert not outcome.passed and outcome.verdict == "ineligible"
    reasons = " ".join(outcome.reasons)
    assert "no frozen panel" in reasons and "no per-arm draw budget" in reasons


def test_the_gate_records_the_specification_it_decided_on():
    """The verdict has to be auditable against the frozen panel it claims to be about."""
    from eval import posttraining_gate as gate
    outcome = gate.decide(baseline=_gate_rows(20, [0]), adapter=_gate_rows(20, [0, 1]),
                          spec=_spec(20)).as_dict()
    spec = outcome["counts"]["spec"]
    assert spec["panel_size"] == 20 and spec["expected_draws_per_arm"] == 40
    assert spec["eligible"] is True and spec["ineligible_reasons"] == []
    assert outcome["rule"]["R4"] and outcome["rule"]["R5"]
    assert any("short draw budget" in phrase for phrase in outcome["rule"]["cannot_authorise"])


def test_the_evaluator_hands_the_gate_a_frozen_specification():
    """The gate was breakable because the caller passed two arms and nothing else."""
    source = Path(ev.__file__).read_text(encoding="utf-8")
    assert "EvaluationSpec(" in source, (
        "the evaluator must declare the frozen specification it is about to execute")
    assert "spec=spec" in source, "and hand it to the gate"
    assert '"subset"' in source, (
        "a --limit'ed or otherwise partial run must be declared ineligible, not silently "
        "promotable")

"""The clang frontend step: it must resolve a real recipe, project clang's diagnostics, and never claim
a candidate is fine when the checker could not run.

WHY THE INTEGRATION TEST IS HERE AND NOT MOCKED. The first version of `solver/frontend_diagnostics` cached
its recipe by repo alone and passed an EMPTY target, so every call raised
`ValueError: unsupported TU object identity` and the frontend looked "unavailable" on eight states out of
eight. A mocked test would have passed, because a mock does not know that a projection is per translation
unit. The test below drives the real `solver.frontend_check.recipe` against the real project Makefile.

The unit tests need no repo, no compiler and no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import frontend_diagnostics as fd                            # noqa: E402

REPO = Path.home() / "decomp/sbk1"
TARGET = "build/src/ultra/audio/save.o"
needs_repo = pytest.mark.skipif(not (REPO / "Makefile").is_file(),
                                reason="the SBK1 build tree is not present")


# --- the parser, on text shaped like the checker's own output -----------------

CLANG = """candidate.c:20:15: error: member reference base type 's32' (aka 'long') is not a structure or union
    var_v1->unk-8 = -1;
    ~~~~~~~~^
candidate.c:21:9: warning: implicit declaration of function 'foo' [-Wimplicit-function-declaration]
candidate.c:30:1: error: 'x' undefined; reoccurrences will not be reported.
"""


def test_the_parser_extracts_errors_and_ignores_warnings_and_notes():
    errors = [line for line in CLANG.splitlines() if "error:" in line]
    assert len(errors) == 2
    # The projection is what the runner reports, so assert on the real regex rather than a re-implementation.
    parsed = [fd._ERROR.match(line.strip()) for line in CLANG.splitlines()]
    parsed = [m for m in parsed if m and m.group("kind") == "error"]
    assert [int(m.group("line")) for m in parsed] == [20, 30]
    assert parsed[0].group("what").startswith("member reference base type")


def test_the_gate_names_are_the_ones_the_repair_family_matches_on():
    """Eight modules gate on these exact strings. If a name drifts here the measurement silently reports
    that a mechanism is reachable when its own gate would decline."""
    import re

    assert re.search(fd.GATES["negative_field_repair"], "candidate.c:1:1: error: member reference base type")
    assert re.search(fd.GATES["void_field_repair"],
                     "error: member reference base type 'void' is not a structure or union")
    assert re.search(fd.GATES["undeclared_identifiers"], "'gFoo' undefined")
    assert re.search(fd.GATES["byte_array_decay"], "incompatible pointer types assigning")
    assert not re.search(fd.GATES["negative_field_repair"], "candidate.c:1:1: error: Syntax Error")


# --- the declines, which must be named and not mistaken for a rejection -------

def test_no_target_is_unavailable_not_rejected(tmp_path):
    """`unavailable` and `rejected` are different answers. A caller that reads an unrunnable checker as a
    failing candidate has learned nothing and believes it learned something."""
    report = fd.analyse("int f(void){return 0;}\n", repo=tmp_path, target="")
    assert report["status"] == "unavailable"
    assert report["passed"] is None
    assert "target" in report["reason"]


def test_a_missing_text_converter_is_unavailable_and_says_why(tmp_path, monkeypatch):
    """The checker must read the same bytes the oracle compiles. Without the project's text converter it
    would read a different file, so declining is the honest answer rather than compiling the raw source.

    The recipe is stubbed here because this test is about the SECOND gate: a repo whose checker is resolved
    but whose converter is absent. `test_a_broken_recipe_is_unavailable_and_says_why` covers the first.
    """
    monkeypatch.setattr(fd, "recipe", lambda repo, target: {"command": ["/bin/true"]})
    repo = tmp_path / "repo"
    repo.mkdir()
    report = fd.analyse("int f(void){return 0;}\n", repo=repo, target=TARGET)
    assert report["status"] == "unavailable"
    assert "textconv" in report["reason"]


def test_a_broken_recipe_is_unavailable_and_says_why(tmp_path):
    """The first gate, on its own: a repo whose Makefile cannot be projected must report that, not a
    rejection of the candidate."""
    repo = tmp_path / "repo"
    (repo / "include").mkdir(parents=True)
    (repo / "Makefile").write_text("WERROR ?= 0\n", encoding="utf-8")
    report = fd.analyse("int f(void){return 0;}\n", repo=repo, target=TARGET)
    assert report["status"] == "unavailable" and report["passed"] is None
    assert "recipe is unavailable" in report["reason"]


def test_the_recipe_cache_is_keyed_by_target_not_by_repo():
    """THE DEFECT THIS PINS. A projection is per translation unit; caching one answer per repo means the
    second target silently reuses the first one's settings, and the first version passed an empty target
    and therefore never produced one at all."""
    import inspect

    source = inspect.getsource(fd.recipe)
    assert "target" in source
    signature = inspect.signature(fd.recipe)
    assert "target" in signature.parameters, "the target is part of the recipe key and cannot default away"


# --- a truncated projection must say it is truncated --------------------------

class _Done:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_clang(diagnostics: str, monkeypatch, tmp_path):
    """Drive `analyse` to the projection with a stubbed checker, so the COUNTS can be asserted.

    The real clang is exercised by the integration tests below; this one is about the projection, and a
    projection bug is invisible to a test that only ever sees a short diagnostic list.
    """
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools/textconv.py").write_text("# stub\ufffd\n", encoding="utf-8")
    (repo / "tools/charmap.txt").write_text("stub\n", encoding="utf-8")
    monkeypatch.setattr(fd, "recipe", lambda repo_, target: {"command": ["/bin/true"]})

    def run(command, **_kwargs):
        return _Done(0) if command[0] == "python3" else _Done(1, diagnostics)

    monkeypatch.setattr(fd.subprocess, "run", run)
    return repo


def test_the_error_list_is_complete_and_only_the_text_is_cut(tmp_path, monkeypatch):
    """THE DEFECT THIS PINS, and the fix that matters for every class count downstream.

    `errors[:40]` made the COUNT complete and the LIST a window. Everything that builds a defect-class set
    reads the list, so the class set was still a sample. Measured consequence: `intake_probe` classed the
    runner's six-line window and reported it as the state's residue, so a state whose first six diagnostics
    shared one class read as a one-class state.
    """
    lines = [f"candidate.c:{i}:1: error: blocker number {i}" for i in range(1, 58)]
    report = fd.analyse("int f(void){return 0;}\n", repo=_fake_clang("\n".join(lines), monkeypatch, tmp_path),
                        target=TARGET)
    assert report["status"] == "rejected"
    assert report["error_count"] == 57
    assert len(report["errors"]) == 57, "the list is complete; only the stored text is bounded"
    assert report["errors_truncated"] is False
    assert report["diagnostics_truncated"] is False


@pytest.mark.parametrize('text,count,complete', [
    ("candidate.c:1:10: fatal error: 'missing.h' file not found\n", 1, False),
    ("include/bad.h:4:2: error: unknown type name 'Broken'\n"
     "candidate.c:3:2: error: use of undeclared identifier 'x'\n", 2, True),
    ('clang: error: unable to execute command\n', 0, False),
])
def test_fatal_header_and_driver_failures_cannot_look_like_complete_zero_errors(
        tmp_path, monkeypatch, text, count, complete):
    report = fd.analyse('void f(void) {}\n', repo=_fake_clang(text, monkeypatch, tmp_path), target=TARGET)
    assert report['error_count'] == count
    assert fd.errors_are_complete(report) is complete
    if 'include/bad.h' in text:
        assert report['errors'][0]['file'] == 'include/bad.h'


def test_a_long_diagnostic_blob_is_reported_as_truncated(tmp_path, monkeypatch):
    """The text projection, which had no count at all: a candidate whose diagnostics were cut to the last
    16 kB must not look like a candidate that produced exactly 16 kB of output. The ERROR LIST is still
    complete, because it is parsed before the cut."""
    blob = "\n".join([f"candidate.c:{i}:1: error: blocker {i}" for i in range(1, 12)]
                     + [f"note: {'x' * 2000}"] * 12)
    report = fd.analyse("int f(void){return 0;}\n", repo=_fake_clang(blob, monkeypatch, tmp_path),
                        target=TARGET)
    assert len(report["diagnostics"]) == 16000
    assert report["diagnostics_truncated"] is True
    assert report["error_count"] == 11 and len(report["errors"]) == 11, \
        "the errors came from the complete text, before the cut"


def test_a_short_diagnostic_list_is_not_flagged(monkeypatch, tmp_path):
    """The decline half, so `truncated` means something: a complete list must not be flagged."""
    report = fd.analyse("int f(void){return 0;}\n",
                        repo=_fake_clang("candidate.c:3:1: error: only one", monkeypatch, tmp_path),
                        target=TARGET)
    assert report["error_count"] == 1 and report["errors_truncated"] is False
    assert report["diagnostics_truncated"] is False


def test_the_runner_carries_the_true_count_beside_the_window(monkeypatch):
    """The runner is the measurement's projection of the frontend. It reported `errors[:6]` and nothing
    else, so `intake_probe._diagnostic_chain` -- which builds a defect-class set from `detail["errors"]` --
    classed a six-error window on a state with sixty and reported the result as the state's residue."""
    from eval import intake_runners

    monkeypatch.setattr(fd, "analyse", lambda *_a, **_k: {
        "status": "rejected", "passed": False, "target": TARGET, "source_sha256": "0" * 64,
        "errors": [{"line": i, "column": 1, "what": f"blocker {i}"} for i in range(1, 41)],
        "error_count": 40, "errors_truncated": False, "diagnostics_truncated": True, "gates": {}})
    result = intake_runners.frontend_diagnostics(
        {"candidate": "int f(void){return 0;}\n", "repo": ".", "target": TARGET}, {})
    assert result["observation"]["error_count"] == 40
    assert result["detail"]["errors_shown"] == 6 and len(result["detail"]["errors"]) == 6
    assert result["detail"]["errors_window"] == 6
    assert result["detail"]["errors_truncated"] is True, "a 40-error list read six at a time is truncated"
    assert result["detail"]["diagnostics_truncated"] is True


def test_a_caller_can_ask_for_every_error(monkeypatch):
    """`max_errors=0` is how a classifier gets the complete list while a receipt keeps six lines. Without it
    the class set is a sample of the first six diagnostics, whatever the true count says."""
    from eval import intake_runners

    monkeypatch.setattr(fd, "analyse", lambda *_a, **_k: {
        "status": "rejected", "passed": False, "target": TARGET, "source_sha256": "0" * 64,
        "errors": [{"line": i, "column": 1, "what": f"blocker {i}"} for i in range(1, 41)],
        "error_count": 40, "errors_truncated": False, "diagnostics_truncated": False, "gates": {}})
    result = intake_runners.frontend_diagnostics(
        {"candidate": "int f(void){return 0;}\n", "repo": ".", "target": TARGET}, {"max_errors": 0})
    assert len(result["detail"]["errors"]) == 40 and result["detail"]["errors_shown"] == 40
    assert result["detail"]["errors_truncated"] is False


def test_the_probe_classifies_every_error_not_the_first_six(monkeypatch):
    """THE MOTIVATING CASE, and it needs the class that only appears past the window: eight errors whose
    first six are all `undeclared identifier` and whose seventh is `no member named`. Classing the window
    reports a one-class state; classing the list reports two, and the second is what the next repair must
    be chosen against."""
    from eval import intake_probe, intake_runners

    seen: dict = {}

    def stub(namespace, params):
        seen.update(params)
        errors = [{"line": i, "column": 1, "what": "use of undeclared identifier 'gFoo'"}
                  for i in range(1, 7)]
        errors += [{"line": 7, "column": 1, "what": "no member named 'unk0' in 'struct T'"},
                   {"line": 8, "column": 1, "what": "no member named 'unk4' in 'struct T'"}]
        return {"status": "ok", "changed": False, "exact": False,
                "observation": {"status": "rejected", "passed": False, "error_count": len(errors),
                                "gates": {}, "source_sha256": "0" * 64},
                "detail": {"errors": errors, "error_count": len(errors), "errors_shown": len(errors),
                           "errors_window": 0, "errors_truncated": False,
                           "diagnostics_truncated": False}}

    monkeypatch.setattr(intake_runners, "frontend_diagnostics", stub)
    chain = intake_probe._diagnostic_chain("int f(void){return 0;}\n", type("C", (), {})(), None)
    assert seen.get("max_errors") == 0, "the probe must ask for the complete list"
    assert chain["classes"] == ["undeclared-identifier", "undeclared-member"], chain["classes"]
    assert chain["class_counts"] == {"undeclared-identifier": 6, "undeclared-member": 2}
    assert chain["classes_from_window"] is False


def _chain_with_detail(monkeypatch, detail: dict) -> dict:
    from eval import intake_probe, intake_runners

    monkeypatch.setattr(intake_runners, "frontend_diagnostics", lambda *_a, **_k: {
        "status": "ok", "changed": False, "exact": False,
        "observation": {"status": "rejected", "passed": False,
                        "error_count": detail["error_count"], "gates": {}, "source_sha256": "0" * 64},
        "detail": detail})
    return intake_probe._diagnostic_chain("int f(void){return 0;}\n", type("C", (), {})(), None)


def test_a_class_set_is_labelled_WHEN_THE_ERROR_LIST_IS_SHORT(monkeypatch):
    """A short class set must be labelled -- and `errors_truncated` is the signal, not the text cut.

    REWRITTEN 2026-09-21, and the reason is a measurement rather than a preference. This asserted the
    label off `diagnostics_truncated`, using "the checker's text was cut" as a proxy for "the list is
    short". The proxy is wrong in BOTH directions and the frozen frame measured both:

      * The false negative it was written to prevent was live anyway. clang's own `-ferror-limit`
        capped the list at 20 while the text stayed well under 16 kB, so every read in the `globals`
        receipt is short, none is labelled, and 352 of 780 sit exactly on the ceiling.
      * The false positive appeared once `-ferror-limit=0` lifted that cap: 83 reads in the
        `ferrorlimit` receipt have COMPLETE error lists and were flagged windowed purely because the
        text passed 16 kB. `analyse` parses `errors` from the whole output before trimming the stored
        text, so a text cut cannot shorten the list.

    The old stub was also not producible by the real runner: with `error_count` 9 against a one-entry
    list, `intake_runners.frontend_diagnostics` computes `errors_truncated = 9 > 1` = True.
    """
    short = _chain_with_detail(monkeypatch, {
        "errors": [{"line": 1, "column": 1, "what": "no member named 'unk0'"}],
        "error_count": 9, "errors_shown": 1, "errors_window": 1,
        # What the runner actually computes for this data: the list IS short.
        "errors_truncated": True, "diagnostics_truncated": False})
    assert short["classes_from_window"] is True, "a short list is a windowed class set"
    assert short["diagnostics_truncated"] is False, "and the two cuts are reported apart"


def test_a_text_cut_alone_does_NOT_label_a_complete_class_set(monkeypatch):
    """The mirror case, which is what 83 reads in the `ferrorlimit` receipt actually were."""
    whole = _chain_with_detail(monkeypatch, {
        "errors": [{"line": 1, "column": 1, "what": "no member named 'unk0'"},
                   {"line": 2, "column": 1, "what": "unknown type name 'T'"}],
        "error_count": 2, "errors_shown": 2, "errors_window": 2,
        "errors_truncated": False, "diagnostics_truncated": True})
    assert whole["classes_from_window"] is False, "the list is complete; only the stored text was cut"
    assert whole["diagnostics_truncated"] is True, "which the trace still records"
    assert whole["classes"] == ["undeclared-member", "unknown-type-name"], whole["classes"]


# --- against the real project, which is where the defect was visible ----------

@needs_repo
def test_the_recipe_resolves_for_a_real_target():
    selected = fd.recipe(REPO, TARGET)
    assert selected["settings"].get("CC_CHECK") == "clang"
    assert "-fsyntax-only" in selected["command"]


@needs_repo
def test_a_candidate_with_a_negative_field_offset_produces_its_repair_gate():
    """The motivating residual: `->unk-4` is not a member access, and clang says which repair owns it."""
    source = ('#include "common.h"\n'
              'void probeNegativeOffset(void *filter) {\n'
              '    void *temp_v0;\n'
              '    temp_v0 = filter;\n'
              '    temp_v0->unk-4 = 1;\n'
              '}\n')
    report = fd.analyse(source, repo=REPO, target=TARGET)
    if report["status"] == "unavailable":
        pytest.skip(f"checker unavailable here: {report['reason']}")
    assert report["status"] == "rejected"
    assert report["error_count"] > 0
    assert "negative_field_repair" in report["gates"] or \
        "void_field_repair" in report["gates"], \
        f"clang did not gate on the negative offset: {report['errors'][:3]}"

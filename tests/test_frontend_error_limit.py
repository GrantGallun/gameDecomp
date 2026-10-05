"""The checker's OWN error ceiling, which made a complete-looking class set a 20-error window.

THE DEFECT. Everything that ranks a repair reads a defect-CLASS SET built from the errors the clang
checker reports: `solver/frontend_diagnostics.analyse` -> `eval/intake_probe._diagnostic_chain` ->
`eval/fault_history` -> `FAULT-HISTOGRAM.md`. clang stops after `-ferror-limit` errors, **20 by
default**, and the recipe in `solver/frontend_check.recipe` passed no such flag. So the list was capped
at 20, the `fatal error: too many errors emitted, stopping now` notice is a `kind` that `_ERROR` does
not match and was parsed as nothing, and `errors_truncated=False` was returned unconditionally.

This is the same bug the project already fixed twice further out -- the runner's six-line window, then
`errors[:40]` -- one level deeper, and it was the only one still live.

MEASURED, with the real clang, 30 `undeclared_N()` calls followed by one `p->no_such_member`:

    default            19 errors, then the fatal notice; `undeclared-member` NEVER APPEARS
    -ferror-limit=0    31 errors, and `no member named` is there

So a whole class was invisible because of WHERE IT SAT IN THE FILE. Member accesses come after
declarations, which is exactly why the histogram's late classes are the suspicious ones:
`undeclared-member` visible at step 0 in 27 states and **cleared in 0**, `member-on-typed-pointer` in
62. Their `masked_then_revealed` counts are partly this artifact -- clearing earlier errors moves later
ones under the ceiling, which reads identically to a wall falling.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import frontend_diagnostics as fd                                 # noqa: E402


def _source_with_a_late_class(undeclared: int = 30) -> str:
    calls = "\n".join(f"    undeclared_{i}();" for i in range(undeclared))
    return textwrap.dedent(f"""\
        struct S {{ int a; }};
        void f(struct S *p) {{
        {calls}
            p->no_such_member = 1;
        }}
        """)


@pytest.mark.skipif(not shutil.which("clang"), reason="the project's checker is clang")
def test_the_default_limit_hides_a_whole_class_and_the_flag_reveals_it(tmp_path):
    """THE MOTIVATING RESIDUAL. This is the measurement the fix exists for, run against real clang."""
    candidate = tmp_path / "candidate.c"
    candidate.write_text(_source_with_a_late_class(), encoding="utf-8")
    base = [shutil.which("clang"), "-fsyntax-only", "-fno-color-diagnostics"]

    capped = subprocess.run([*base, str(candidate)], capture_output=True, text=True, timeout=60)
    whole = subprocess.run([*base, "-ferror-limit=0", str(candidate)],
                           capture_output=True, text=True, timeout=60)
    capped_text = capped.stdout + capped.stderr
    whole_text = whole.stdout + whole.stderr

    assert "too many errors emitted" in capped_text, "the default ceiling is what this test is about"
    assert "no member named" not in capped_text, "the late class is invisible under the default"
    assert "no member named" in whole_text, "and visible without the ceiling"
    assert whole_text.count(": error:") > capped_text.count(": error:")


def test_the_recipe_puts_the_flag_on_the_command(monkeypatch, tmp_path):
    """The flag has to be ON THE COMMAND, because that is the only place it can act.

    Driven through `recipe()` with a stubbed `make`, so the assertion is about the argv the checker is
    actually invoked with rather than about the text of the module.
    """
    from solver import frontend_check

    fields = {"CC_CHECK": "clang", "CC_CHECK_FLAGS": "-fsyntax-only", "CC_CHECK_WARNINGS": "",
              "CC_CHECK_INCLUDES": "-Iinclude", "C_DEFINES": "-DF3DEX_GBI",
              "CC_CHECK_MIPS_DEFINES": "-D_MIPS_SZLONG=32"}
    stdout = "".join(f"__DECOMP_{k}__={v}\n" for k, v in fields.items())
    monkeypatch.setattr(frontend_check.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, ""))
    monkeypatch.setattr(frontend_check.shutil, "which", lambda name: str(tmp_path / "clang"))
    (tmp_path / "clang").write_bytes(b"\x7fELF")
    # `projection()` refuses a Makefile whose warning policy it does not recognise, so the stub carries
    # the project's actual one: WERROR defaults to 0, and -Werror is added only when it is turned on.
    makefile = tmp_path / "Makefile"
    makefile.write_text("WERROR ?= 0\n"
                        "CC_CHECK := clang\n"
                        "CC_CHECK_FLAGS := -fsyntax-only\n"
                        "CC_CHECK_WARNINGS :=\n"
                        "CC_CHECK_INCLUDES := -Iinclude\n"
                        "C_DEFINES := -DF3DEX_GBI\n"
                        "CC_CHECK_MIPS_DEFINES := -D_MIPS_SZLONG=32\n"
                        "ifneq ($(WERROR),0)\n"
                        "\tCC_CHECK_WARNINGS += -Werror\n"
                        "endif\n"
                        "all:\n", encoding="utf-8")

    resolved = frontend_check.recipe(str(tmp_path), makefile.read_text(), "build/src/f.o")
    assert "-ferror-limit=0" in resolved["command"], resolved["command"]
    assert "-fsyntax-only" in resolved["command"]


def test_the_checkers_own_truncation_notice_is_read_rather_than_parsed_as_nothing():
    """`fatal error:` is a kind `_ERROR` does not match, so the cut used to leave no trace at all."""
    assert fd._ERROR.match(
        "candidate.c:5:3: error: use of undeclared identifier 'gX'"), "ordinary errors still parse"
    assert not fd._ERROR.match(
        "candidate.c:9:1: fatal error: too many errors emitted, stopping now"), \
        "the notice is not an error line, which is exactly why it needs its own pattern"
    assert fd._LIMIT.search("candidate.c:9:1: fatal error: too many errors emitted, stopping now")
    assert not fd._LIMIT.search("candidate.c:5:3: error: use of undeclared identifier 'gX'")


def test_a_truncated_report_says_so_instead_of_claiming_completeness(monkeypatch, tmp_path):
    """`errors_truncated` comes from the checker now; it used to be the literal `False`."""
    truncated = ("candidate.c:1:1: error: use of undeclared identifier 'gA'\n"
                 "candidate.c:2:1: fatal error: too many errors emitted, stopping now\n")
    clean = "candidate.c:1:1: error: use of undeclared identifier 'gA'\n"

    for text, expected in ((truncated, True), (clean, False)):
        report = _analyse_with_output(monkeypatch, tmp_path, text)
        assert report["errors_truncated"] is expected, text
        # The notice is never counted as one of the defects.
        assert report["error_count"] == 1, report["errors"]


def _analyse_with_output(monkeypatch, tmp_path, diagnostics: str) -> dict:
    """Drive `analyse` with a stubbed checker process, so no compiler or Makefile is needed."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True, exist_ok=True)
    (repo / "tools" / "textconv.py").write_text(
        "import sys, shutil; shutil.copyfile(sys.argv[2], sys.argv[3])\n", encoding="utf-8")
    (repo / "tools" / "charmap.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(fd, "recipe", lambda repo, target: {"command": ["stub"], "gates": {}})

    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if argv and argv[0] == "stub":
            return subprocess.CompletedProcess(argv, 1, diagnostics, "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(fd.subprocess, "run", fake_run)
    return fd.analyse("void f(void) {}\n", repo=repo, target="build/src/f.o", timeout=10)


def test_a_text_cut_does_not_mark_the_class_set_as_windowed(monkeypatch, tmp_path):
    """THE MIRROR DEFECT, found by fixing the first one.

    `analyse` parses `errors` from the checker's WHOLE output and only then trims the stored text to
    16 kB, so a text cut cannot shorten the error list. `_diagnostic_chain` was flagging
    `classes_from_window` off `diagnostics_truncated`, which marked 83 complete class sets in the
    `ferrorlimit` receipt as windowed. A label that is wrong in either direction is not a label.
    """
    long_what = "x" * 200
    text = "".join(f"candidate.c:{i}:1: error: use of undeclared identifier '{long_what}{i}'\n"
                   for i in range(120))
    report = _analyse_with_output(monkeypatch, tmp_path, text)
    assert len(report["diagnostics"]) <= 16000
    assert report["diagnostics_truncated"] is True, "this fixture exists to cut the text"
    assert report["errors_truncated"] is False, "the error list is parsed before the text is trimmed"
    assert report["error_count"] == 120, "and it is complete"

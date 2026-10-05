"""The rescue ladder: deterministic C89 first, the model's self-fix only when that fails."""
from __future__ import annotations

from dataclasses import dataclass

from solver import compile_fallback as cf


@dataclass
class Att:
    compiled: bool
    compiler_stderr: str = ""


def compiler(log):
    def compile_fn(label, code):
        log.append(label)
        bad = "int16_t" in code or "BROKEN" in code
        return Att(not bad, "cfe: Error: candidate.c, line 3: Syntax Error" if bad else "")
    return compile_fn


def test_deterministic_c89_rescues_dialect_without_calling_the_model() -> None:
    log, calls = [], []
    src = "void f(void) {\n    int16_t x = 1;\n}\n"
    out = cf.rescue(src, "Syntax Error", function="f", compile_fn=compiler(log),
                    fix_fn=lambda p: calls.append(p) or ("", 0.0))
    assert out.rescued_by == "c89" and "s16 x" in out.source
    assert calls == [], "the model is not asked when the free fix works"


def test_model_self_fix_is_the_fallback_and_sees_the_error() -> None:
    log, prompts = [], []

    def fix(prompt):
        prompts.append(prompt)
        return "```c\nvoid f(void) {\n    s32 x;\n}\n```".split("```c\n")[1].split("```")[0], 7.5
    src = "void f(void) {\n    BROKEN x;\n}\n"
    out = cf.rescue(src, "cfe: Error: line 2: Syntax Error", function="f",
                    compile_fn=compiler(log), fix_fn=fix)
    assert out.rescued_by == "model-fix-1" and out.model_seconds == 7.5
    assert "Syntax Error" in prompts[0] and "BROKEN x" in prompts[0]


def test_model_rounds_are_bounded_and_each_sees_the_new_error() -> None:
    log, prompts = [], []
    fixes = iter(["void f(void) { BROKEN a; }", "void f(void) { BROKEN b; }", "unused"])

    def fix(prompt):
        prompts.append(prompt)
        return next(fixes), 1.0
    out = cf.rescue("void f(void) { BROKEN; }", "Syntax Error", function="f",
                    compile_fn=compiler(log), fix_fn=fix, max_fix_rounds=2)
    assert out.attempt is None and len(prompts) == 2 and out.model_seconds == 2.0
    assert "BROKEN a" in prompts[1], "round two repairs round one's output, not the original"
    assert [s["step"] for s in out.steps if "compiled" in s][-2:] == ["model-fix-1", "model-fix-2"]


def test_without_a_model_the_ladder_stops_after_the_deterministic_rungs() -> None:
    out = cf.rescue("void f(void) { BROKEN; }", "Syntax Error", function="f",
                    compile_fn=compiler([]))
    assert out.attempt is None and out.rescued_by is None and out.model_seconds == 0.0


def test_context_rung_restores_the_includes_the_model_dropped() -> None:
    log = []

    def compile_fn(label, code):
        log.append(label)
        ok = '#include "common.h"' in code and "typedef s32 s32" not in code
        return Att(ok, "" if ok else "cfe: Error: candidate.c, line 2: Syntax Error")
    src = "typedef s32 s32;\nvoid f(void) {\n    s16 x;\n}\n"
    out = cf.rescue(src, "Syntax Error", function="f", compile_fn=compile_fn,
                    context_includes=["common.h", "game/ui.h"])
    assert out.rescued_by == "context"
    assert out.source.startswith('#include "common.h"\n#include "game/ui.h"\n')
    assert "typedef s32 s32" not in out.source and "s16 x;" in out.source
    kept = cf.with_context('#include "common.h"\nvoid f(void) {}\n', ["common.h"])
    assert kept.count('#include "common.h"') == 1, "an include already present is not duplicated"


def test_a_candidate_with_no_single_definition_fails_its_rung_instead_of_crashing() -> None:
    out = cf.rescue("int x = 1;\n", "Syntax Error", function="f", compile_fn=compiler([]))
    assert out.attempt is not None or out.steps, "the ladder ran to completion"


# --- the convergent model loop ------------------------------------------------------------------

@dataclass
class FAtt:
    compiled: bool
    compiler_stderr: str = ""
    frontend: dict | None = None


def clang_compiler(errors_for):
    """compile_fn whose clang diagnostics list one error per entry of errors_for(code)."""
    def compile_fn(label, code):
        errs = errors_for(code)
        diag = "\n".join(f"candidate.c:{i + 3}:5: error: {e}" for i, e in enumerate(errs))
        return FAtt(not errs, "cfe: Error: candidate.c, line 3: Syntax Error" if errs else "",
                    {"diagnostics": diag})
    return compile_fn


def test_the_prompt_carries_clangs_reason_not_just_idos_syntax_error(tmp_path) -> None:
    (tmp_path / "include" / "PR").mkdir(parents=True)
    (tmp_path / "include" / "PR" / "ultratypes.h").write_text("typedef signed short s16;\n")
    prompts = []
    compile_fn = clang_compiler(lambda code: ["unknown type name 's16'"] if "fixed" not in code else [])

    def fix(prompt):
        prompts.append(prompt)
        return "void f(void) {\n    s16 x; /* fixed */\n}\n", 1.0
    out = cf.rescue("void f(void) {\n    s16 x;\n}\n", "Syntax Error", function="f",
                    compile_fn=compile_fn, fix_fn=fix, repo=tmp_path)
    assert out.rescued_by == "model-fix-1"
    assert "unknown type name 's16'" in prompts[0]
    assert "s16: declared in PR/ultratypes.h" in prompts[0], "the model is told where it lives"


def test_a_fix_that_deletes_the_failing_code_is_rejected_and_explained() -> None:
    prompts = []
    compile_fn = clang_compiler(lambda code: ["use of undeclared identifier 'g'"] if "g(" in code
                                and "restored" not in code else [])
    answers = iter(["void f(void) {\n}\n", "void f(void) {\n    g(); /* restored */\n}\n"])

    def fix(prompt):
        prompts.append(prompt)
        return next(answers), 1.0
    out = cf.rescue("void f(void) {\n    g();\n}\n", "Syntax Error", function="f",
                    compile_fn=compile_fn, fix_fn=fix)
    assert out.steps[-2]["rejected"].startswith("your fix deleted calls to ['g']")
    assert "YOUR LAST ATTEMPT WAS REJECTED" in prompts[1]
    assert out.rescued_by == "model-fix-2" and "g();" in out.source


def test_the_loop_keeps_going_while_errors_fall_and_stops_when_they_do_not() -> None:
    # Each round removes one of three errors: the loop must pass round 2 and finish.
    falling = iter([["e1", "e2"], ["e1"], []])
    state = {"errs": ["e1", "e2", "e3"]}

    def compile_fn(label, code):
        if label.startswith("model-fix"):
            state["errs"] = next(falling)
        errs = state["errs"]
        return FAtt(not errs, "Syntax Error" if errs else "",
                    {"diagnostics": "\n".join(f"x.c:1:1: error: {e}" for e in errs)})
    n = iter(range(100))
    out = cf.rescue("void f(void) { a; }", "Syntax Error", function="f", compile_fn=compile_fn,
                    fix_fn=lambda p: (f"void f(void) {{ a; /* {next(n)} */ }}", 1.0))
    assert out.rescued_by == "model-fix-3", "three rounds of progress, past the old 2-round cap"

    stuck = clang_compiler(lambda code: ["e1", "e2"])
    m = iter(range(100))
    out = cf.rescue("void f(void) { a; }", "Syntax Error", function="f", compile_fn=stuck,
                    fix_fn=lambda p: (f"void f(void) {{ a; /* {next(m)} */ }}", 1.0), patience=2)
    assert out.attempt is None and out.steps[-1] == {"step": "stopped",
                                                     "reason": "no progress in 2 rounds"}
    assert sum(1 for s in out.steps if s["step"].startswith("model-fix")) == 2


def test_unknown_names_are_read_from_both_compilers() -> None:
    text = ("line 3:5: error: unknown type name 'Actor'\n"
            "line 9:1: error: use of undeclared identifier 'gFoo'\n"
            "cfe: Error: candidate.c, line 4: 'gBar' undefined; reoccurrences will not be reported.")
    assert cf.unknown_names(text) == ["Actor", "gFoo", "gBar"]


def test_admit_passes_a_compiled_candidate_through_untouched() -> None:
    att = Att(True)
    out = cf.admit("void f(void) {}", att, function="f", compile_fn=lambda l, c: 1 / 0)
    assert out.attempt is att and out.source == "void f(void) {}" and out.steps == []


def test_admit_runs_the_ladder_on_a_failed_candidate_and_returns_the_new_source() -> None:
    log = []
    out = cf.admit("void f(void) {\n    int16_t x;\n}\n", Att(False, "Syntax Error"), function="f",
                   compile_fn=compiler(log))
    assert out.rescued_by == "c89" and "s16 x" in out.source and log == ["c89"]


def test_binary_branch_intake_context_never_adds_game_headers(monkeypatch) -> None:
    from solver import project_headers
    monkeypatch.setattr(project_headers, "context_headers",
                        lambda repo, fn, asm: ["game/race/race_state.h", "PR/os.h"])
    assert cf.intake_includes("r", "f", "", assisted=True) == ["common.h", "game/race/race_state.h",
                                                               "PR/os.h"]
    assert cf.intake_includes("r", "f", "", assisted=False) == ["common.h", "PR/os.h"]


# --- the compile-chain rung and model facts (2026-09-28, never_compiled_chain.out) ------------------

def placeholder_compiler(log):
    """IDO rejects m2c's `?` placeholder type; anything else compiles."""
    def compile_fn(label, code):
        log.append(label)
        bad = [i for i, line in enumerate(code.splitlines(), 1) if line.lstrip().startswith("?")]
        return Att(not bad, f"cfe: Error: candidate.c, line {bad[0]}: Syntax Error" if bad else "")
    return compile_fn


M2C_DRAFT = "? g(s32);                                         /* extern */\nvoid f(void) {\n    g(1);\n}\n"


def test_the_chain_rung_repairs_an_m2c_placeholder_before_the_model_is_asked() -> None:
    log, calls = [], []
    out = cf.rescue(M2C_DRAFT, "cfe: Error: candidate.c, line 1: Syntax Error", function="f",
                    compile_fn=placeholder_compiler(log), fix_fn=lambda p: calls.append(p) or ("", 0.0))
    assert out.attempt is not None and out.rescued_by.startswith("chain:"), out.steps
    assert "?" not in out.source.splitlines()[0]
    assert calls == [], "the model is not asked when the deterministic chain compiles it"


def test_without_the_chain_rung_the_same_draft_falls_through_to_the_model() -> None:
    log, calls = [], []
    out = cf.rescue(M2C_DRAFT, "cfe: Error: candidate.c, line 1: Syntax Error", function="f",
                    compile_fn=placeholder_compiler(log), fix_fn=lambda p: calls.append(p) or ("", 0.0),
                    chain=False, max_fix_rounds=1)
    assert out.attempt is None and len(calls) == 1


def test_facts_reach_the_model_only_when_given() -> None:
    prompts = []
    fix = lambda p: prompts.append(p) or ("", 0.0)
    cf.rescue("void f(void) { BROKEN; }", "Syntax Error", function="f", compile_fn=compiler([]),
              fix_fn=fix, max_fix_rounds=1, facts="TARGET ASSEMBLY:\nlw v0,0x24(a0)")
    cf.rescue("void f(void) { BROKEN; }", "Syntax Error", function="f", compile_fn=compiler([]),
              fix_fn=fix, max_fix_rounds=1)
    assert "lw v0,0x24(a0)" in prompts[0] and "TARGET ASSEMBLY" not in prompts[1]

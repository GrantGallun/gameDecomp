"""Two working passes that had no action wrapper, and the trap that sits between them.

THE RESIDUAL. `wide-intake-smi.json`, ranked by sole blocker: `undeclared-function` is the ONLY
remaining class in 8 states, 6 of them a single error. Read apart, they are THREE things with three
different answers, and the messages say which:

    M2C_BREAK(6);                          __ll_div, __ll_mod        m2c's spelling for MIPS `break`
    M2C_MEMCPY_ALIGNED(dst, src, 0x30);    __osViSwapContext         a bulk copy -> solver/m2c_copy
    enqueueSoundEffect(0x18, 0x32);        updateRaceUiResultsBanner…  a real function -> a prototype
    drawMenuFillRectangle(...);            drawMainMenuModeSelectIcons          "
    temp_ret = __ll_mul(...)               updateBouncingItemProjectile  return USED -> abstain

THE TRAP, and it is the same shape as the `unaligned` one: the cheap reading of this whole bucket is
"declare the missing function". Do that to `M2C_MEMCPY_ALIGNED` or `M2C_BREAK` and the candidate
compiles and emits a `jal` to a symbol that is not in the ROM — a state measurably FURTHER from exact,
reported as a conversion. `test_a_macro_is_never_given_a_prototype` is what stops that.

Neither repair is new here. `solver/m2c_copy.propose` and
`solver/compile_recovery.scalar_header_prototypes` are complete, guarded, and reachable from
`solver/modelrepair.py` — the MODEL path. The deterministic intake route never called either. That is
the third instance of this exact gap, after `lower_bitcasts`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import intake_runners                                               # noqa: E402
from eval.tool_runners import FAILED, NO_CHANGE, NOT_APPLICABLE, OK           # noqa: E402

# The `__osViSwapContext` shape: a whole-struct copy m2c spelled as its own macro.
ALIGNED_COPY = """\
void __osViSwapContext(void) {
    M2C_MEMCPY_ALIGNED(__osViNext, __osViCurr, 0x30);
}
"""


def test_the_aligned_copy_action_fires_on_its_motivating_residual():
    """THE MOTIVATING CASE: `__osViSwapContext`, its single remaining error."""
    result = intake_runners.m2c_aligned_copy(
        {"candidate": ALIGNED_COPY, "function": "__osViSwapContext"}, {})
    assert result["status"] == OK, result["reason"]
    assert result["changed"] is True
    assert "M2C_MEMCPY_ALIGNED" not in result["source"], result["source"]
    assert result["detail"]["plans"], "the lowering has to report what it did"


def test_a_candidate_without_the_macro_says_so():
    result = intake_runners.m2c_aligned_copy(
        {"candidate": "void f(void) { g(); }\n", "function": "f"}, {})
    assert result["status"] == NO_CHANGE
    assert result["reason"] == "the candidate has no M2C_MEMCPY_ALIGNED call"


def test_a_non_literal_size_declines_with_its_reason():
    """`m2c_copy`'s own guard: the size must be a literal multiple of 4, because the lowering is a
    bounded loop and a variable bound is not one."""
    source = "void f(void) {\n    M2C_MEMCPY_ALIGNED(a, b, n);\n}\n"
    result = intake_runners.m2c_aligned_copy({"candidate": source, "function": "f"}, {})
    assert result["changed"] is False
    assert result["status"] == NO_CHANGE
    assert "literal size" in result["detail"]["declined_reason"]


def test_a_raising_pass_is_failed_not_no_change(monkeypatch):
    from solver import m2c_copy

    monkeypatch.setattr(m2c_copy, "propose", lambda *_a: (_ for _ in ()).throw(ValueError("nope")))
    result = intake_runners.m2c_aligned_copy(
        {"candidate": ALIGNED_COPY, "function": "__osViSwapContext"}, {})
    assert result["status"] == FAILED
    assert "ValueError" in result["reason"]


# --- the prototype action ------------------------------------------------------

def _frontend(monkeypatch, diagnostics: str, status: str = "rejected"):
    from solver import frontend_diagnostics as fd

    monkeypatch.setattr(fd, "analyse", lambda *a, **k: {
        "status": status, "passed": False, "error_count": 1, "gates": {},
        "source_sha256": "0" * 64, "errors": [], "diagnostics": diagnostics,
        "diagnostics_truncated": False, "errors_truncated": False,
        "reason": "unavailable" if status == "unavailable" else ""})


def _prototypes(monkeypatch, result):
    from solver import compile_recovery

    monkeypatch.setattr(compile_recovery, "scalar_header_prototypes", lambda *a: result)


def test_the_prototype_action_fires_and_names_the_header_it_came_from(monkeypatch):
    """THE MOTIVATING CASE, and the receipt has to carry the tier with the gain."""
    source = "void f(void) {\n    enqueueSoundEffect(0x18, 0x32);\n}\n"
    _frontend(monkeypatch, "candidate.c:2:5: error: implicit declaration of function "
                           "'enqueueSoundEffect'\n")
    _prototypes(monkeypatch, ("extern void enqueueSoundEffect(int, int);\n" + source,
                              {"prototypes": [{"name": "enqueueSoundEffect",
                                               "prototype": "extern void enqueueSoundEffect(int, int);",
                                               "headers": [{"include": "game/engine/sound.h",
                                                            "sha256": "ab" * 32}]}],
                               "declines": []}))
    result = intake_runners.header_prototypes(
        {"candidate": source, "repo": ".", "target": "build/src/f.o"}, {})
    assert result["status"] == OK, result["reason"]
    assert result["changed"] is True
    assert "extern void enqueueSoundEffect" in result["source"]
    assert "HEADER-ASSISTED" in result["detail"]["authority"], result["detail"]["authority"]


def test_a_public_sdk_header_is_not_labelled_header_assisted(monkeypatch):
    """`include/PR/**` is the published SDK; `include/game/**` is the decomp team's reconstruction.
    CLAUDE.md puts those on opposite sides of the contamination line."""
    source = "void f(void) {\n    osWritebackDCache(p, n);\n}\n"
    _frontend(monkeypatch, "candidate.c:2:5: error: implicit declaration of function "
                           "'osWritebackDCache'\n")
    _prototypes(monkeypatch, ("extern void osWritebackDCache(void *, int);\n" + source,
                              {"prototypes": [{"name": "osWritebackDCache",
                                               "prototype": "extern void osWritebackDCache(void *, int);",
                                               "headers": [{"include": "PR/os.h", "sha256": "cd" * 32}]}],
                               "declines": []}))
    result = intake_runners.header_prototypes(
        {"candidate": source, "repo": ".", "target": "build/src/f.o"}, {})
    assert result["changed"] is True
    assert "HEADER-ASSISTED" not in result["detail"]["authority"]
    assert "SDK" in result["detail"]["authority"]


def test_a_macro_is_never_given_a_prototype(monkeypatch):
    """THE TRAP. `M2C_MEMCPY_ALIGNED` has no ROM symbol, so a prototype would compile into a `jal` to
    nothing -- a conversion that moves AWAY from exact. The underlying pass declines because no header
    declares the name; this asserts the action carries that decline instead of inventing one."""
    source = "void f(void) {\n    M2C_MEMCPY_ALIGNED(a, b, 0x30);\n}\n"
    _frontend(monkeypatch, "candidate.c:2:5: error: implicit declaration of function "
                           "'M2C_MEMCPY_ALIGNED'\n")
    _prototypes(monkeypatch, (source, {"prototypes": [], "declines": [
        {"name": "M2C_MEMCPY_ALIGNED", "reason": "requires one primitive scalar header signature"}]}))
    result = intake_runners.header_prototypes(
        {"candidate": source, "repo": ".", "target": "build/src/f.o"}, {})
    assert result["changed"] is False
    assert result["source"] == source, "not one byte"
    assert "primitive scalar header signature" in result["detail"]["declined_reason"]


def test_no_implicit_declaration_is_a_clean_no_op(monkeypatch):
    _frontend(monkeypatch, "candidate.c:1:1: error: unknown type name 'Actor'\n")
    result = intake_runners.header_prototypes(
        {"candidate": "void f(void){}\n", "repo": ".", "target": "build/src/f.o"}, {})
    assert result["status"] == NO_CHANGE
    assert result["reason"] == "the checker reports no implicitly declared function"


def test_an_unavailable_checker_is_named_rather_than_skipped(monkeypatch):
    _frontend(monkeypatch, "", status="unavailable")
    result = intake_runners.header_prototypes(
        {"candidate": "void f(void){}\n", "repo": ".", "target": "build/src/f.o"}, {})
    assert result["status"] == NOT_APPLICABLE
    assert "checker" in result["reason"]


def test_both_actions_are_registered_and_name_their_missing_inputs():
    registry: dict = {}
    names = intake_runners.register(registry)
    for label in ("eval.intake_runners.m2c_aligned_copy", "eval.intake_runners.header_prototypes"):
        assert label in names, label

    assert intake_runners.m2c_aligned_copy({"candidate": "x"}, {})["status"] == NOT_APPLICABLE
    no_target = intake_runners.header_prototypes({"candidate": "x", "repo": "."}, {})
    assert no_target["status"] == NOT_APPLICABLE
    assert "target" in no_target["reason"]


# --- the guard that declined on the whole class it was written for --------------

def test_the_project_scalar_typedefs_count_as_primitive(monkeypatch, tmp_path):
    """THE MOTIVATING RESIDUAL for the guard fix, asserted through the pass rather than its source.

    `scalar_header_prototypes` kept a prototype only when every type in it was a raw C keyword. The
    game is written in `s16`/`u8`/`f32`, so `void enqueueSoundEffect(s16, s16);` declined -- and on the
    frozen frame the pass fired on 13 states, converted 0, and every decline said `requires one
    primitive scalar header signature`. A pass that declines on its entire class reads as a pass with
    nothing to do.
    """
    from solver import compile_recovery, project_headers

    header = tmp_path / "include" / "game" / "sound.h"
    header.parent.mkdir(parents=True)
    header.write_text("void enqueueSoundEffect(s16, s16);\n", encoding="utf-8")

    class Declaration:
        def __init__(self, prototype, include):
            self.prototype, self.include = prototype, include

    def declarations(repo, name, all_variants=False, max_results=13):
        if name == "enqueueSoundEffect":
            return [Declaration("void enqueueSoundEffect(s16, s16);", "game/sound.h")]
        if name == "takesAnActor":
            return [Declaration("void takesAnActor(Actor *);", "game/sound.h")]
        return []

    monkeypatch.setattr(project_headers, "declarations", declarations)
    diagnostics = ("candidate.c:2:5: error: implicit declaration of function 'enqueueSoundEffect'\n"
                   "candidate.c:3:5: error: implicit declaration of function 'takesAnActor'\n")
    source = ("void f(void) {\n"
              "    enqueueSoundEffect(1, 2);\n"
              "    takesAnActor(a);\n"
              "}\n")
    out, report = compile_recovery.scalar_header_prototypes(tmp_path, source, diagnostics)

    taken = {p["name"] for p in report["prototypes"]}
    assert taken == {"enqueueSoundEffect"}, report["prototypes"]
    assert "void enqueueSoundEffect(s16, s16);" in out, out
    # THE GUARD STILL REFUSES what it was protecting against: a pointer parameter carries a `*` token.
    assert [d["name"] for d in report["declines"]] == ["takesAnActor"], report["declines"]
    # AND THE REASON NAMES THE ACTUAL CAUSE. One message for five conditions is what sent a whole
    # iteration at the primitive set while the real cause was an absent declaration.
    assert "not all primitive scalars" in report["declines"][0]["reason"]
    assert "'Actor'" in report["declines"][0]["reason"], report["declines"][0]["reason"]


def test_an_absent_declaration_is_not_reported_as_an_unsuitable_signature(monkeypatch, tmp_path):
    """THE MESSAGE THAT COST AN ITERATION.

    `enqueueSoundEffect` and `drawMenuFillRectangle` are declared in NO header in the target repo --
    `project_headers.declarations` returns 0 for both. The pass reported `requires one primitive scalar
    header signature`, which reads as "the signature was unsuitable", so the widened-primitive fix was
    aimed at a cause that was not there. Those states are correctly UNREACHABLE by header projection:
    a prototype would have to come from evidence, and inventing parameter types is inventing.
    """
    from solver import compile_recovery, project_headers

    monkeypatch.setattr(project_headers, "declarations", lambda *a, **k: [])
    source = ("void f(void) {\n"
              "    enqueueSoundEffect(1, 2);\n"
              "}\n")
    diagnostics = ("candidate.c:2:5: error: implicit declaration of function "
                   "'enqueueSoundEffect'\n")
    _, report = compile_recovery.scalar_header_prototypes(tmp_path, source, diagnostics)
    assert report["prototypes"] == []
    decline = report["declines"][0]
    assert decline["declarations"] == 0
    assert "no header declares this name" in decline["reason"], decline["reason"]
    assert "primitive" not in decline["reason"], "the old message blamed the signature"


def test_a_pointer_signature_still_declines():
    """`signature()` emits `*` as its own token, which is what keeps the widened set safe."""
    from solver import type_transaction

    assert type_transaction.signature("void f(Actor *a);", "f") == (("void",), (("Actor", "*"),))
    assert type_transaction.signature("void g(s16 a, u8 b);", "g") == (
        ("void",), (("s16",), ("u8",)))

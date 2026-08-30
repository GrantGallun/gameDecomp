"""Unit tests for the pure logic — the parts that were only ever spot-checked.

Every case here encodes something that was verified by hand during development
and could silently regress. Several encode bugs that actually happened:

  - a size gate that looked applied and was not (both branches now asserted)
  - routing that sent a relocation mismatch to the permuter
  - a detector that must stay SILENT on negative controls, not just fire on
    positives -- a hint that fires wrongly is worse than one that never fires

Run:  python3 -m pytest tests/ -q
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from kb import tms                                    # noqa: E402
from patterns.catalog import (decode_array_stride,    # noqa: E402
                              detect_narrow_params, hints_for_asm,
                              target_frame_size)
from solver.llm import extract_c                      # noqa: E402
from solver.pipeline import route_for, triage         # noqa: E402
from solver.workspace import EXACT_RE, SCORE_RE       # noqa: E402


# ----------------------------------------------------------------- routing

@pytest.mark.parametrize("verdict,score,expected", [
    # The case score-band routing gets wrong: instructions identical, symbols
    # wrong. Scores ~100 and must NOT go to the permuter.
    ("words-identical", 99.999, "relocation"),
    ("relocation-layout-mismatch", 99.0, "relocation"),
    ("unknown-relocation", 96.0, "relocation"),
    # Things the permuter can actually move.
    ("allocation-mismatch", 96.0, "permute"),
    ("register-permutation", 92.0, "permute"),
    # Source-shape problems.
    ("frame-layout-mismatch", 88.0, "retype"),
    ("constant-mismatch", 70.0, "retype"),
    ("structure-mismatch", 47.0, "reshape"),
    # Mixed verdicts read "mixed(structural:4, register:8)" and must not crash.
    ("mixed(structural:4, register:8)", 85.0, "retype"),
    # No verdict -> fall back to score bands.
    ("", 97.0, "permute"),
    ("", 85.0, "retype"),
    ("", 40.0, "reshape"),
    # A perfect instruction score without an exact verdict is usually a
    # relocation mismatch, never proof of a match.
    ("", 100.0, "relocation"),
    ("structure-mismatch", 100.0, "reshape"),
])
def test_route_for(verdict, score, expected):
    assert route_for(verdict, score) == expected


def test_exact_verdict_always_wins_regardless_of_score_or_diagnosis():
    assert route_for("structure-mismatch", 42.0, exact=True) == "matched"


def test_triage_bands():
    assert triage(100.0) == "relocation"
    assert triage(42.0, exact=True) == "matched"
    assert triage(99.9) == "permute"
    assert triage(80.0) == "retype"
    assert triage(79.9) == "reshape"


# ------------------------------------------------------------ score parsing

def test_score_and_exact_parsing():
    out = ("Raw decompiled assembly of ref.c: ref_object_dump.s\n"
           "Score: 100.000% (0 differences)\n"
           "Exact match: yes\n"
           "Verified exact match: yes\n")
    assert float(SCORE_RE.search(out).group(1)) == 100.0
    assert EXACT_RE.search(out).group(1) == "yes"


def test_perfect_score_is_not_exact_match():
    """The real trap: 0 differences and still not byte-exact.

    A candidate can match every instruction and differ in its relocations.
    Score alone must never be treated as proof of a match.
    """
    out = "Score: 99.999% (0 differences)\nVerified exact match: no\n"
    assert float(SCORE_RE.search(out).group(1)) > 99.9
    assert EXACT_RE.search(out).group(1) == "no"


# --------------------------------------------------------------- extraction

def test_extract_c_prefers_last_real_file():
    """Reasoning models emit fragments mid-trace; the answer comes last."""
    text = ("thinking about it\n```c\nint fragment;\n```\n"
            "actually here it is\n```c\nvoid f(void) { return; }\n```\n")
    assert "void f(void)" in extract_c(text)
    assert "fragment" not in extract_c(text)


def test_extract_c_strips_think_blocks():
    text = "<think>```c\nint decoy(void){return 1;}\n```</think>\n```c\nint real(void){return 2;}\n```"
    got = extract_c(text)
    assert "real" in got and "decoy" not in got


def test_extract_c_without_fences():
    assert "int f(void)" in extract_c("int f(void) { return 0; }")


# ---------------------------------------------------------------- detectors

def test_decode_array_stride():
    """sll 2; addu self; sll 2  ==  (i*4 + i)*4  ==  i*20."""
    asm = ("sll    $t6, $a0, 0x2\n"
           "addu   $t6, $t6, $a0\n"
           "sll    $t6, $t6, 0x2\n")
    assert 20 in decode_array_stride(asm)


def test_decode_array_stride_silent_without_pattern():
    assert decode_array_stride("lw $v0, 0x0($a0)\njr $ra\n") == []


def test_detect_narrow_params_positive_and_negative():
    """Positive AND negative control -- absence carries information too."""
    narrow = ("sw     $a0, 0x0($sp)\n"
              "andi   $t6, $a0, 0xffff\n")
    assert detect_narrow_params(narrow) == {"a0": 2}

    byte_wide = ("sw     $a1, 0x4($sp)\n"
                 "andi   $t7, $a1, 0xff\n")
    assert detect_narrow_params(byte_wide) == {"a1": 1}

    # An s32 parameter emits neither instruction.
    assert detect_narrow_params("lui $t6, 0x8012\nlh $t6, 0x1b50($t6)\n") == {}


def test_frame_size_extraction():
    assert target_frame_size("addiu  $sp, $sp, -24\nsw $ra, 0x14($sp)\n") == 24
    assert target_frame_size("lui $v0, 0x8005\njr $ra\n") is None


def test_hints_mention_frame_and_stride():
    asm = ("addiu  $sp, $sp, -32\n"
           "sll    $t6, $a0, 0x2\n"
           "addu   $t6, $t6, $a0\n"
           "sll    $t6, $t6, 0x2\n")
    hints = hints_for_asm(asm)
    assert "32" in hints and "20" in hints


# ------------------------------------------------------------- tms invariants

@pytest.fixture
def kb():
    conn = sqlite3.connect(":memory:")
    conn.executescript((Path(__file__).parent.parent / "kb" / "schema.sql").read_text())
    conn.execute("INSERT INTO extraction (target, rom_sha1, elf_path,"
                 " tool_versions, created_at) VALUES ('t','x','x','{}',0)")
    conn.execute("INSERT INTO evidence (extraction_id, kind, addr, op)"
                 " VALUES (1,'mem_access',100,'lw')")
    yield conn
    conn.close()


def test_uncited_inference_rejected(kb):
    with pytest.raises(tms.CitationRequired):
        tms.assert_inference(kb, "field", "struct:A@0x0", "s32", [], origin="model")


def test_dangling_citation_rejected(kb):
    """Citing evidence that does not exist is not a citation."""
    with pytest.raises(tms.CitationRequired):
        tms.assert_inference(kb, "field", "struct:A@0x0", "s32", [999999],
                             origin="model")


def test_human_origin_exempt(kb):
    """Facts imported from the reference repo carry provenance in the repo."""
    assert tms.assert_inference(kb, "field", "struct:A@0x0", "s32", [],
                                origin="human") > 0


def test_cited_inference_accepted(kb):
    assert tms.assert_inference(kb, "field", "struct:A@0x0", "s32", [1],
                                origin="model") > 0


def test_evidence_is_immutable(kb):
    tms.guard_evidence_immutable(kb)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        kb.execute("UPDATE evidence SET op='sw' WHERE id=1")
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        kb.execute("DELETE FROM evidence WHERE id=1")


def test_retraction_cascades_and_demotes(kb):
    root = tms.assert_inference(kb, "field", "struct:A@0x0", "s32", [1])
    derived = tms.assert_inference(kb, "struct_size", "struct:A", "8", [1],
                                   depends_on=[root])
    kb.execute("INSERT INTO functions (addr, name, state) VALUES (1,'f','matched')")
    tms.record_dependency(kb, 1, [derived])

    assert tms.matched_count(kb) == 1
    r = tms.retract(kb, root, "test")

    assert derived in r.cascaded, "retraction must cascade to dependent claims"
    assert 1 in r.demoted, "a match relying on a retracted claim must be demoted"
    assert tms.matched_count(kb) == 0


def test_ratchet_rolls_back_a_losing_change(kb):
    kb.execute("INSERT INTO functions (addr, name, state) VALUES (1,'f','matched')")
    kb.commit()

    def lose(conn):
        conn.execute("UPDATE functions SET state='attempted' WHERE addr=1")
        return []

    committed, before, after = tms.ratchet(kb, lose)
    assert committed is False
    assert before == 1 and after == 0
    assert tms.matched_count(kb) == 1, "rollback must restore the match"


def test_ratchet_commits_a_neutral_change(kb):
    kb.execute("INSERT INTO functions (addr, name, state) VALUES (1,'f','matched')")
    kb.commit()
    committed, before, after = tms.ratchet(
        kb, lambda c: c.execute("UPDATE functions SET size=8 WHERE addr=1") and [])
    assert committed is True and before == after == 1


def test_every_prompt_template_actually_formats():
    """A .format() template containing literal braces raises at runtime.

    The do-while guidance inserted `for (;;) { body; if (!cond) break; }` into
    FIRST_PROMPT, and .format() parsed those braces as a field, so
    build_prompt raised ValueError for EVERY function. Unit tests did not catch
    it because none of them called build_prompt -- the pipeline was broken and
    "all tests pass" was true the whole time.
    """
    from solver import escalate, refine, shifts

    for mod, attr in [(refine, "FIRST_PROMPT"),
                      (shifts, "REGION_PROMPT"), (shifts, "COMPOSE_PROMPT"),
                      (shifts, "COMPRESSED_PROMPT"), (shifts, "SLICE_PROMPT"),
                      (shifts, "FINAL_PROMPT")]:
        tmpl = getattr(mod, attr, None)
        if tmpl is None:
            continue
        import string
        fields = {f for _, f, _, _ in string.Formatter().parse(tmpl) if f}
        try:
            tmpl.format(**{f: "X" for f in fields})
        except (ValueError, KeyError, IndexError) as exc:
            raise AssertionError(
                f"{mod.__name__}.{attr} does not format: {exc}. "
                f"Literal braces must be doubled.") from exc


def test_every_python_file_in_the_repo_parses():
    """A syntax error in a module no test imports is invisible.

    eval/run_set.py -- the MAIN eval runner -- carried an unterminated f-string
    for dozens of commits, because a \n written through a shell heredoc became
    a real newline. Nothing imported it, so nothing noticed, and every
    experiment since then was run through one-off harnesses instead.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    broken = []
    for py in root.rglob("*.py"):
        parts = set(py.parts)
        if parts & {".git", ".venv", "site-packages", "__pycache__"}:
            continue
        try:
            ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            broken.append(f"{py.relative_to(root)}:{exc.lineno}: {exc.msg}")
    assert not broken, "files with syntax errors:\n  " + "\n  ".join(broken)


def test_a_failing_sample_does_not_abandon_the_function():
    """A union run died on all 38 functions with draws=0, because
    qwen2.5-coder rejects `think` with a 400 on the chat endpoint and the
    unhandled exception discarded the gpt-oss sample that had already
    succeeded. An infrastructure failure on ONE proposer is not a result for
    the whole function."""
    import inspect
    from solver import pipeline
    src = inspect.getsource(pipeline.solve)
    gen = src.index("llm.generate")
    before = src[:gen]
    assert "try:" in before.split("for i in range")[-1], \
        "the sample loop calls llm.generate without a try/except"

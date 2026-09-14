from pathlib import Path

import pytest

from tools import score_repo_function


def test_candidate_keeps_includes_and_only_requested_definition(tmp_path: Path):
    source = tmp_path / "source.c"
    source.write_text(
        '#include "common.h"\n\n'
        'void other(void) { nope(); }\n'
        's32 wanted(s32 x) { return x + 1; }\n',
        encoding="utf-8")
    candidate = score_repo_function.candidate_from_file(source, "wanted")
    assert '#include "common.h"' in candidate
    assert "s32 wanted(s32 x)" in candidate
    assert "void other" not in candidate


def test_missing_symbol_is_rejected(tmp_path: Path):
    source = tmp_path / "source.c"
    source.write_text("void present(void) {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="found 0"):
        score_repo_function.candidate_from_file(source, "missing")


def test_explicit_local_declaration_range_is_included(tmp_path: Path):
    source = tmp_path / "source.c"
    source.write_text(
        '#include "common.h"\n'
        'typedef struct { s32 x; } Local;\n'
        'extern Local gLocal;\n'
        's32 wanted(void) { return gLocal.x; }\n', encoding="utf-8")
    candidate = score_repo_function.candidate_from_file(
        source, "wanted", [(2, 3)])
    assert "typedef struct" in candidate
    assert "extern Local gLocal" in candidate


def test_explicit_standalone_declaration_is_included(tmp_path: Path):
    source = tmp_path / "source.c"
    source.write_text("s32 wanted(void) { return gValue; }\n", encoding="utf-8")
    candidate = score_repo_function.candidate_from_file(
        source, "wanted", extra_declarations=["extern s32 gValue;"])
    assert "extern s32 gValue;" in candidate


def test_explicit_source_implementation_include_can_be_excluded(tmp_path: Path):
    source = tmp_path / "source.c"
    source.write_text(
        '#include "common.h"\n'
        '#include "late_implementation.inc.c"\n\n'
        's32 anchor;\n'
        's32 wanted(void) { return 1; }\n', encoding="utf-8")
    candidate = score_repo_function.candidate_from_file(
        source, "wanted", excluded_includes=[".inc.c"])
    assert '#include "common.h"' in candidate
    assert "late_implementation.inc.c" not in candidate


def test_line_range_validation():
    assert score_repo_function.parse_line_range("2:4") == (2, 4)
    with pytest.raises(ValueError, match="invalid"):
        score_repo_function.parse_line_range("4:2")


def test_do_while_rewrites_to_bottom_tested_for_loop():
    source = "void f(void) { do { tick(); } while (again()); }"
    rewritten = score_repo_function.rewrite_do_while(source)
    assert "do {" not in rewritten
    assert "for (;;)" in rewritten
    assert "if (!(again())) break;" in rewritten


def test_entry_guarded_do_while_can_rewrite_as_while():
    source = "void f(void) { if (again()) { do { tick(); } while (again()); } }"
    rewritten = score_repo_function.rewrite_do_while(source, style="while")
    assert "do {" not in rewritten
    assert "for (;;)" not in rewritten
    assert "while (again())" in rewritten
    assert "if (!(again()))" not in rewritten


def test_do_while_with_continue_is_rejected():
    source = "void f(void) { do { if (x) continue; tick(); } while (again()); }"
    with pytest.raises(ValueError, match="loop-level continue"):
        score_repo_function.rewrite_do_while(source)


def test_empty_constant_zero_do_while_is_erased_without_a_loop():
    source = "void f(void) { before(); do { } while (0); after(); }"
    rewritten = score_repo_function.rewrite_do_while(source)
    assert "do {" not in rewritten
    assert "for (;;)" not in rewritten
    assert "while (0)" not in rewritten
    assert "before(); {} after();" in rewritten


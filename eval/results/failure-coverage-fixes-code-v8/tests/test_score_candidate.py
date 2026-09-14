import pytest

from tools import score_candidate


def test_unique_replace_requires_exactly_one_occurrence():
    assert score_candidate.rewrite_source(
        "old + keep", replacements=[("old", "new")]
    ) == "new + keep"
    with pytest.raises(ValueError, match="found 2"):
        score_candidate.rewrite_source(
            "old + old", replacements=[("old", "new")]
        )


def test_replace_all_supports_stable_address_symbol_aliases():
    source = "extern int semantic; int f(void) { return semantic; }"
    rewritten = score_candidate.rewrite_source(
        source, replace_all=[("semantic", "D_80001234")]
    )
    assert rewritten.count("D_80001234") == 2
    assert "semantic" not in rewritten


def test_header_symbol_is_masked_only_while_includes_are_processed():
    source = (
        '#include "one.h"\n'
        '#include "two.h"\n'
        "extern void historical(int value);\n"
    )
    rewritten = score_candidate.rewrite_source(
        source, header_masks=[("historical", "historical_header_decl")]
    )
    assert rewritten.startswith(
        "#define historical historical_header_decl\n#include")
    assert rewritten.index("#undef historical") > rewritten.index("two.h")
    assert rewritten.index("#undef historical") < rewritten.index("extern void")


def test_header_mask_rejects_invalid_identifiers():
    with pytest.raises(ValueError, match="C identifiers"):
        score_candidate.rewrite_source(
            '#include "one.h"\n', header_masks=[("bad-name", "alias")]
        )

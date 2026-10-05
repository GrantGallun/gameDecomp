"""Blind statement edits that touch no faulty line are skipped, only with a source-bound attribution."""
from solver import edit_locality

SRC = "void f(int a, int b) {\n    x = a + b;\n    y = b + a;\n    z = 1;\n}\n"


def test_off_target_only_for_blind_statement_families_away_from_faulty_lines():
    swapped_line2 = SRC.replace("a + b", "b + a", 1)
    assert edit_locality.edited_lines(SRC, swapped_line2) == {2}
    assert edit_locality.off_target(SRC, swapped_line2, ("commutative",), {3})
    assert not edit_locality.off_target(SRC, swapped_line2, ("commutative",), {2})
    assert not edit_locality.off_target(SRC, swapped_line2, ("local_type",), {3})    # declarations are exempt
    assert not edit_locality.off_target(SRC, swapped_line2, ("commutative",), None)   # unknown: never skip
    assert not edit_locality.off_target(SRC, swapped_line2, ("commutative",), set())


def test_residual_lines_need_an_attribution_of_this_source():
    assert edit_locality.residual_lines(SRC, "d", None) is None
    assert edit_locality.residual_lines(SRC, "d", {"status": "verified", "source_sha256": "0" * 64}) is None

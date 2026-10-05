"""Undoing loop strength reduction fires on its motivating residual and declines when the shape is not exact."""
from pathlib import Path

from solver import regalloc_mutations, strength_inverse

SOURCE = (Path(__file__).parent / "fixtures" / "strength_inverse_waitCourseSelectRecordsClose.c").read_text()
NAME = "waitCourseSelectRecordsClose"


def candidates(source=SOURCE):
    return dict(strength_inverse.variants(source, NAME))


def test_fires_on_the_pointer_walk_and_writes_the_object_exact_form():
    made = candidates()
    through = made["index_form:var_s1->D_801121E0[var_s0]:through_gCurrentMenuCameraObject"]
    # Compiled 2026-09-23, this candidate was object-exact (eval/results/register-steer-20260923).
    assert "gCurrentMenuCameraObject = &D_801121E0[var_s0];" in through
    assert "gCurrentMenuCameraObject->update();" in through
    assert "var_s1 =" not in through and "+ 0xB0" not in through
    assert "D_801121E0[var_s0].update();" in made["index_form:var_s1->D_801121E0[var_s0]"]


def test_declines_without_a_counter_or_with_a_use_after_the_step():
    assert not candidates(SOURCE.replace("var_s0 += 1;", "var_s0 += 2;"))
    moved = SOURCE.replace("            var_s1->update();\n", "")
    moved = moved.replace("        } while (var_s0 < (s32) gPlayerCount);",
                          "            var_s1->update();\n        } while (var_s0 < (s32) gPlayerCount);")
    assert not candidates(moved)


def test_joins_the_mutation_stream():
    kinds = {k for _l, k, _c in regalloc_mutations.variants(SOURCE, NAME, "")}
    assert "index_form" in kinds

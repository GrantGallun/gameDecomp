"""The engine's header module must see plain typedefs, not only braced ones.

The same blind spot existed in two places -- `project_headers._typedef_definition` and
`project_headers.dependency_headers` -- and both were invisible for the same reason a detector that
returns nothing looks like a detector with nothing to do. The motivating case is real and named:
`include/game/math/geometry.h:23` is `typedef s16 Mat3x3[9];`, `drawRacePlayerModel-2`'s draft uses
`Mat3x3 rotation;`, and the engine offered neither the definition nor the header.

These use the repository when it is present, because a synthetic fixture cannot show that the fix
finds the declaration the ENGINE actually needed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from solver import project_headers

REPO = Path.home() / "decomp" / "sbk1"
GEOMETRY = REPO / "include" / "game" / "math" / "geometry.h"


def test_plain_typedef_is_returned_whole(tmp_path: Path):
    text = "typedef s16 Mat3x3[9];\nint other;\n"
    assert project_headers._typedef_definition(text, "Mat3x3") == "typedef s16 Mat3x3[9];"


def test_scalar_and_pointer_typedefs(tmp_path: Path):
    assert project_headers._typedef_definition("typedef u32 Handle;\n", "Handle") == \
        "typedef u32 Handle;"
    assert project_headers._typedef_definition("typedef struct S *SRef;\n", "SRef") == \
        "typedef struct S *SRef;"


def test_braced_form_still_works(tmp_path: Path):
    text = "typedef struct Thing {\n    s32 x;\n} Thing;\n"
    assert project_headers._typedef_definition(text, "Thing") == text.strip()


def test_declines_on_a_name_used_only_as_a_parameter(tmp_path: Path):
    """`typedef s32 (*Fn)(RaceCourseSurface *);` does not declare RaceCourseSurface."""
    text = "typedef s32 (*Fn)(RaceCourseSurface *);\n"
    assert project_headers._typedef_definition(text, "RaceCourseSurface") is None


def test_the_offline_shape_is_the_one_missing_before(tmp_path: Path):
    """Pin the counterfactual: without the plain arm this returns None, which is what made the
    definition invisible. If a later refactor drops the arm, this fails on the same input."""
    text = "typedef s16 Mat3x3[9];\n"
    assert project_headers._typedef_definition(text, "Mat3x3") is not None


@pytest.mark.skipif(not GEOMETRY.is_file(), reason="decomp tree not present")
def test_fires_on_the_real_geometry_header():
    """The motivating residual, against the repository rather than a fixture."""
    definition = project_headers._typedef_definition(GEOMETRY.read_text(errors="replace"), "Mat3x3")
    assert definition is not None
    assert definition.startswith("typedef")
    assert "Mat3x3" in definition and definition.rstrip().endswith(";")


@pytest.mark.skipif(not GEOMETRY.is_file(), reason="decomp tree not present")
def test_the_header_is_found_for_a_draft_that_uses_the_type():
    """Through the engine-facing caller. `dependency_headers` collects `Type *` uses, so a draft with
    `Mat3x3 *p` must now resolve a header -- before the fix the braced-only pattern found none.

    Note the scope this does NOT cover: `type_names` is built from `Type *` uses, so a draft that
    writes `Mat3x3 rotation;` (no pointer) is not a type use this function sees at all. That is the
    caller's existing scope, not a regression from this change, and it is why the assertion below
    uses a pointer.
    """
    draft = "void f(void) {\n    Mat3x3 *p;\n    p = 0;\n}\n"
    found = project_headers.dependency_headers(REPO, draft)
    assert any("geometry.h" in path for path in found), found

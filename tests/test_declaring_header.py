"""`declaring_header` must recognise every spelling the project actually uses.

Written because it recognised two of three and returned None for `Mat3x3`, which IS declared at
`include/game/math/geometry.h:23` as `typedef s16 Mat3x3[9];`. A None from this function is not a
neutral answer -- it routes a name to the `unk`-only path as "unattributable from evidence" -- so the
miss was a misclassification, not just a missed fix. Both halves are asserted: the spellings that must
fire, and the neighbouring shapes that must NOT be read as declarations.
"""
from __future__ import annotations

from eval import header_admission as ha


def _repo(tmp_path, body: str):
    (tmp_path / "include" / "game").mkdir(parents=True)
    (tmp_path / "include" / "game" / "types.h").write_text(body, encoding="utf-8")
    return tmp_path


def test_fires_on_a_plain_typedef(tmp_path):
    """The motivating residual: geometry.h:23."""
    repo = _repo(tmp_path, "typedef s16 Mat3x3[9];\n")
    assert ha.declaring_header(repo, "Mat3x3") == "game/types.h"


def test_fires_on_a_scalar_typedef(tmp_path):
    repo = _repo(tmp_path, "typedef unsigned long u64x;\n")
    assert ha.declaring_header(repo, "u64x") == "game/types.h"


def test_fires_on_a_pointer_typedef(tmp_path):
    repo = _repo(tmp_path, "typedef struct Foo *FooRef;\n")
    assert ha.declaring_header(repo, "FooRef") == "game/types.h"


def test_fires_on_a_tagged_struct(tmp_path):
    repo = _repo(tmp_path, "typedef struct PlayerCommandState {\n    s32 x;\n} PlayerCommandState;\n")
    assert ha.declaring_header(repo, "PlayerCommandState") == "game/types.h"


def test_fires_on_a_bare_struct_tag(tmp_path):
    repo = _repo(tmp_path, "struct ALFilter {\n    s32 type;\n};\n")
    assert ha.declaring_header(repo, "ALFilter") == "game/types.h"


def test_declines_on_a_name_used_only_as_a_parameter(tmp_path):
    """`typedef s32 (*Fn)(RaceCourseSurface *);` does not declare RaceCourseSurface, and the repository
    proves the distinction matters: that name is declared file-locally in a project `.c`, and treating
    a parameter use as a declaration would send it down the header route where no header exists."""
    repo = _repo(tmp_path, "typedef s32 (*Fn)(RaceCourseSurface *);\n")
    assert ha.declaring_header(repo, "RaceCourseSurface") is None


def test_declines_on_a_name_that_only_appears_in_an_identifier(tmp_path):
    """Function names carry the type name as a substring all over this project --
    `initRaceCourseSurfaceData` is not a declaration of `RaceCourseSurface`."""
    repo = _repo(tmp_path, "void initRaceCourseSurfaceData(void);\ns32 getRaceCourseSurfaceHeight(void);\n")
    assert ha.declaring_header(repo, "RaceCourseSurface") is None

"""The SBK2 GCC oracle must call finished code exact AND reject near-misses the text cannot see."""
import shutil
import tempfile
from pathlib import Path

import pytest

from tools import gcc_oracle as o

TU = "src/animation/bobbing_model.c"
TARGET = "build/src/animation/bobbing_model.o"
NAME = "initTiltingModelTask"
BODY = "    setCleanupCallback(cleanupTiltingModelTask);\n    setCallback(updateTiltingModelTask);"

ready = ((o.REPO / "tools" / "gcc_kmc" / "gcc").exists() and (o.REPO / TARGET).exists()
         and shutil.which(o.OBJDUMP) is not None)
needs_sbk2 = pytest.mark.skipif(not ready, reason="SBK2 build, KMC GCC or objdump unavailable")


def _compile(source: str):
    rec = o.recipe(o.REPO)
    tmp = Path(tempfile.mkdtemp(prefix="gcc-oracle-test-"))
    obj = tmp / "candidate.o"
    ok, stderr = o.compile_unit(o.REPO, rec, TU, source, obj)
    assert ok, stderr
    return o.score(o.REPO / TARGET, obj, NAME), obj


@pytest.fixture(scope="module")
def reference() -> str:
    source = (o.REPO / TU).read_text()
    assert BODY in source, "reference body changed; update the fixture"
    return source


@needs_sbk2
def test_finished_source_is_exact(reference):
    result, _obj = _compile(reference)
    assert result["exact"] and result["diff"] == ""


@needs_sbk2
def test_swapped_callbacks_fail_on_bytes_though_the_text_is_identical(reference):
    """Both load `%lo(.text)`; only the addend in the instruction differs."""
    swapped = reference.replace(BODY, "    setCleanupCallback(updateTiltingModelTask);\n"
                                      "    setCallback(cleanupTiltingModelTask);")
    result, obj = _compile(swapped)
    assert not result["exact"]
    assert o.normalized(o.REPO / TARGET, NAME) == o.normalized(obj, NAME)
    assert result["residual"] == "bytes-only"


@needs_sbk2
def test_a_wrong_call_target_fails_on_relocations_though_the_bytes_are_identical(reference):
    """`jal 0` either way in an unlinked object; only the R_MIPS_26 symbol differs."""
    wrong = reference.replace(BODY, "    setCallback(cleanupTiltingModelTask);\n"
                                    "    setCallback(updateTiltingModelTask);")
    result, obj = _compile(wrong)
    target_words, target_relocs = o.fingerprint(o.REPO / TARGET, NAME)
    words, relocs = o.fingerprint(obj, NAME)
    assert not result["exact"]
    assert words == target_words and relocs != target_relocs


@needs_sbk2
def test_recipe_is_read_from_the_makefile_without_executing_it():
    rec = o.recipe(o.REPO)
    assert "-mips3" in rec["cflags_base"] and "-O2" in rec["default_opt"]
    assert rec["overrides"]["src/graphics/tiled_sprite_grid.c"] == ["-O0"]
    assert len(rec["makefile_sha256"]) == 64


def test_command_mirrors_the_makefile_rule_for_a_nested_tu():
    rec = {"cflags_base": ["-mips3"], "macros": ["-DNDEBUG"], "iinc": ["-I", "include"],
           "default_opt": ["-O2"], "overrides": {"src/graphics/tiled_sprite_grid.c": ["-O0"]}}
    cmd = o.command(rec, "src/graphics/tiled_sprite_grid.c")
    assert cmd[:3] == ["tools/gcc_kmc/gcc", "-mips3", "-O0"]
    assert ["-I", "src/graphics/"] == cmd[4:6] and "build/graphics/" in cmd
    assert o.command(rec, "src/main.c")[2] == "-O2"

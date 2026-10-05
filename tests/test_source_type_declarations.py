"""`source_type_declarations` must recover a corroborated declaration and decline everything else.

The declines carry the weight here. The whole reason this pass is defensible is that it refuses when the
binary disagrees -- measured on the frame, `ShopMenuWidgetActor` annotates `state` at 0x1c while the binary
accesses that parameter at 24, 26, 44..56, and that refusal is the difference between recovering a
declaration and inventing a layout.

No repo, no compiler and no network: every case is inline text.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import source_type_declarations as std                        # noqa: E402


def make_repo(tmp_path: Path, relative: str, text: str) -> Path:
    path = tmp_path / "src" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path


DECLARATION = """\
struct PickupShardParticleActor {
    char pad0[0x10];
    /* 0x10 */ u16 spawnOffsetIndex;
    char pad12[6];
    /* 0x18 */ Vec3i pos;
    /* 0x44 */ s8 transformDirty;
    char pad45[3];
    /* 0x48 */ void *image;
};

void renderPickupShardParticle(PickupShardParticleActor *arg0) {
    arg0->transformDirty = 1;
    arg0->image = 0;
}
"""

SOURCE = ("void renderPickupShardParticle(PickupShardParticleActor *arg0) {\n"
          "    arg0->transformDirty = 1;\n"
          "    arg0->image = 0;\n"
          "}\n")


def test_recovers_a_declaration_the_binary_corroborates(tmp_path):
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    block, receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=repo,
                                 target="build/src/race/course/props.o", assembly="",
                                 binary_offsets={"param0": {0x44, 0x48}})
    assert block, f"declined: {receipt['declined']}"
    assert "struct PickupShardParticleActor {" in block
    assert receipt["recovered"][0]["type"] == "PickupShardParticleActor"
    assert receipt["corroborated"]["PickupShardParticleActor"] == {"transformDirty": "0x44",
                                                                   "image": "0x48"}


def test_declines_when_the_binary_does_not_touch_the_annotated_offset(tmp_path):
    """THE GATE THAT MATTERS. `ShopMenuWidgetActor` on the frame annotates `state` at 0x1c against binary
    accesses at 24, 26, 44..56; recovering it would place a member where the binary has nothing."""
    repo = make_repo(tmp_path, "menu/shop.c", DECLARATION)
    _block, receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=repo,
                                  target="build/src/menu/shop.o", assembly="",
                                  binary_offsets={"param0": {0x18, 0x30}})
    assert not _block
    assert any("not corroborated" in reason for reason in receipt["declined"]), receipt


def test_declines_on_a_member_the_declaration_does_not_have(tmp_path):
    """m2c can name a member the project's struct has no field for. Recovering the body would then leave
    the draft's own access unresolved, so the honest answer is to say so."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    source = SOURCE.replace("arg0->image = 0;", "arg0->palette = 0;")
    _block, receipt = std.recover(source, function="renderPickupShardParticle", repo=repo,
                                  target="build/src/race/course/props.o", assembly="",
                                  binary_offsets={"param0": {0x44, 0x4C}})
    assert not _block
    assert any("does not declare" in reason for reason in receipt["declined"]), receipt


def test_declines_when_the_type_is_only_forward_declared(tmp_path):
    """A `struct T;` has no members. This is the case that cannot be reached at all, and the receipt has to
    distinguish it from a header-resolution gap."""
    repo = make_repo(tmp_path, "race/course/props.c",
                     "struct PickupShardParticleActor;\n" + SOURCE)
    block, receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=repo,
                                 target="build/src/race/course/props.o", assembly="",
                                 binary_offsets={"param0": {0x44}})
    assert not block
    assert any("no body" in reason for reason in receipt["declined"]), receipt


def test_declines_when_the_target_is_not_a_src_file(tmp_path):
    """A target outside `build/src/**` must not be mapped to a guessed source path: attaching another
    translation unit's types to this draft is worse than declining."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    for target in ("", "build/src/ultra/x.o", "build/asm/x.o", "build/src/../etc/passwd.o"):
        _block, receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=repo,
                                      target=target, assembly="", binary_offsets={"param0": {0x44}})
        assert not _block, target
        assert receipt["declined"], target


def test_a_type_already_declared_in_the_draft_is_left_alone(tmp_path):
    """Recovering a body the draft already carries would duplicate the definition."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    source = ("struct PickupShardParticleActor {\n    char pad0[0x44];\n    s8 transformDirty;\n};\n"
              + SOURCE)
    block, _receipt = std.recover(source, function="renderPickupShardParticle", repo=repo,
                                  target="build/src/race/course/props.o", assembly="",
                                  binary_offsets={"param0": {0x44}})
    assert not block


def test_the_recovered_block_carries_no_function_body(tmp_path):
    """The line between vocabulary and the answer. A block with `) {` in it is a body and must never be
    returned, whatever the binary says."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    block, _receipt = std.recover(SOURCE, function="renderPickupShardParticle", repo=repo,
                                  target="build/src/race/course/props.o", assembly="",
                                  binary_offsets={"param0": {0x44, 0x48}})
    assert block
    assert ") {" not in block and "return" not in block
    assert "Declarations only" in block


def test_a_local_type_is_carried_not_corroborated(tmp_path):
    """`Vec3i pos`-style locals have NO binary evidence path: the assembly reaches a local through a register
    m2c named, not through a `param<i>` slot, and this pass has no mapping for that. Those recoveries are
    CARRIED, and a summary that counts them with corroborated ones makes an unverified recovery look
    verified."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    source = SOURCE.replace("arg0->image = 0;", "arg0->image = 0;")
    source = "void f(PickupShardParticleActor *arg1) {\n    arg1->transformDirty = 1;\n}\n"
    # A second type on a LOCAL of the same file's shape.
    _block, receipt = std.recover(
        "void renderPickupShardParticle(PickupShardParticleActor *arg0) {\n"
        "    ExtraThing *var_v0;\n"
        "    arg0->image = 0;\n"
        "    var_v0->field = 1;\n}\n",
        function="renderPickupShardParticle", repo=make_repo(
            tmp_path / "b", "race/course/props.c",
            DECLARATION + "\nstruct ExtraThing {\n    /* 0x4 */ s32 field;\n};\n"),
        target="build/src/race/course/props.o", assembly="",
        binary_offsets={"param0": {0x48}})
    assert "PickupShardParticleActor" in receipt["corroborated"], receipt
    assert "ExtraThing" in receipt["carried"], receipt
    assert "local" in receipt["carried"]["ExtraThing"]["reason"]
    assert receipt["corroborated_total"] >= 1 and receipt["carried_total"] >= 1


def test_members_without_binary_evidence_are_named_as_the_projects_authority(tmp_path):
    """With no offsets supplied at all, every recovery is carried and the receipt says so rather than
    claiming a check that did not run."""
    repo = make_repo(tmp_path, "race/course/props.c", DECLARATION)
    source = SOURCE.replace("arg0->image = 0;", "arg0->pos = 0;")
    block, receipt = std.recover(source, function="renderPickupShardParticle", repo=repo,
                                 target="build/src/race/course/props.o", assembly="",
                                 binary_offsets={})
    assert block
    assert "PickupShardParticleActor" in receipt["carried"]
    assert receipt["corroborated"] == {}


def test_typedef_spelling_is_returned_intact(tmp_path):
    """A typedef'd body must keep its alias, or the draft's `Type *arg0` no longer names a type."""
    repo = make_repo(tmp_path, "race/ui/effects.c",
                     "typedef struct RaceUiTripleParticleActor {\n"
                     "    /* 0x32 */ u8 matrixDirty;\n"
                     "} RaceUiTripleParticleActor;\n")
    source = ("void renderRaceCourseTripleParticle(RaceUiTripleParticleActor *arg0) {\n"
              "    arg0->matrixDirty = 1;\n}\n")
    block, receipt = std.recover(source, function="renderRaceCourseTripleParticle", repo=repo,
                                 target="build/src/race/ui/effects.o", assembly="",
                                 binary_offsets={"param0": {0x32}})
    assert block
    assert block.rstrip().endswith("} RaceUiTripleParticleActor;")
    assert receipt["corroborated"]["RaceUiTripleParticleActor"] == {"matrixDirty": "0x32"}


@pytest.mark.parametrize("kind", ["struct", "union", "enum"])
def test_all_three_aggregate_kinds_are_recovered(tmp_path, kind):
    repo = make_repo(tmp_path, "a/b.c", f"{kind} T {{\n    /* 0x4 */ s32 field;\n}};\n")
    source = "void f(T *arg0) {\n    arg0->field = 1;\n}\n"
    block, receipt = std.recover(source, function="f", repo=repo, target="build/src/a/b.o",
                                 assembly="", binary_offsets={"param0": {0x4}})
    assert block, receipt["declined"]
    assert f"{kind} T {{" in block

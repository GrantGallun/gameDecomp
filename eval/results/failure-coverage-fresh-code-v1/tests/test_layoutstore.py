"""Tests for struct layout evidence pooled across functions.

The generator must FIRE on its motivating shape, not merely decline correctly
on others -- that asymmetry is what let four passes sit silently dead.
"""

from solver import layoutstore, rewrites


def setup_function(_fn):
    layoutstore.clear()


def teardown_function(_fn):
    layoutstore.clear()


TWO_FIELD = ("typedef struct {\n    u8 a;\n    u8 b;\n} RacePlayer;\n"
             "void f(RacePlayer *p) { p->a = 1; }\n")


def test_store_is_empty_until_loaded():
    """Importing must not change behaviour; the store is opt-in."""
    assert not layoutstore.loaded()
    assert rewrites.shared_layout_rewrites(TWO_FIELD, "") == []


def test_conflicting_widths_collapse_to_unknown():
    layoutstore.record("T", 0x10, 2, "fnA")
    layoutstore.record("T", 0x10, 4, "fnB")
    assert layoutstore._STORE["T"][0x10] is None, "unknown is the default"


def test_exclusion_drops_a_functions_own_evidence():
    layoutstore.record("T", 0x10, 2, "onlyMe")
    layoutstore.record("T", 0x20, 2, "someoneElse")
    assert layoutstore.offsets_for("T") == [0x10, 0x20]
    layoutstore.set_exclusion("onlyMe")
    assert layoutstore.offsets_for("T") == [0x20], \
        "an offset pinned only by the excluded function must not transfer"


def test_shared_layout_fires_on_a_struct_other_functions_pinned():
    layoutstore.record("RacePlayer", 0x18, 1, "fnA")
    layoutstore.record("RacePlayer", 0x1c, 1, "fnB")
    rws = rewrites.shared_layout_rewrites(TWO_FIELD, "")
    assert rws, "two pinned offsets and two declared fields must propose"
    out = rws[0](TWO_FIELD)
    assert "dpad" in out and "RacePlayer" in out


def test_shared_layout_declines_when_evidence_is_thin():
    layoutstore.record("RacePlayer", 0x18, 1, "fnA")
    assert rewrites.shared_layout_rewrites(TWO_FIELD, []) == [] or \
        rewrites.shared_layout_rewrites(TWO_FIELD, "") == []


def test_shared_layout_never_asks_a_field_to_move_earlier():
    """Padding only moves fields later; a backwards map is apply_widths' job."""
    code = ("typedef struct {\n    char pad[0x40];\n    u8 a;\n    u8 b;\n"
            "} RacePlayer;\n")
    layoutstore.record("RacePlayer", 0x0, 1, "fnA")
    layoutstore.record("RacePlayer", 0x4, 1, "fnB")
    assert rewrites.shared_layout_rewrites(code, "") == []


def test_shared_layout_ignores_an_unknown_struct():
    layoutstore.record("SomethingElse", 0x18, 1, "fnA")
    layoutstore.record("SomethingElse", 0x1c, 1, "fnB")
    assert rewrites.shared_layout_rewrites(TWO_FIELD, "") == []

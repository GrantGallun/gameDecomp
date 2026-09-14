"""typepool must FIRE on the residual it was written for.

The motivating measurement: PlayerCommandState is named by 15 leaf drafts and
pools to 36 offsets against 15 for the best single function. Every test here
asserts the pool is WIDER than any one contributor, or that a specific guess
is refused.
"""

import pytest

from solver import typepool


class _Conn:
    """structgen.layout's query only, keyed by the function in params."""

    def __init__(self, per_function):
        self._per = per_function

    def execute(self, _sql, params=()):
        rows = self._per.get(params[0], [])

        class _Cur(list):
            def fetchall(self):
                return list(self)

        return _Cur(rows)


def test_pool_is_wider_than_any_single_contributor():
    conn = _Conn({
        "Fcutoff": [("param0", 0xC2, 2, 1, 1), ("param0", 0xC4, 2, 1, 0)],
        "Fdefa":   [("param0", 0x38, 4, 1, 1), ("param0", 0xFD, 1, 1, 0)],
        "Fgoto":   [("param0", 0xC2, 4, 1, 1)],
    })
    uses = {"PlayerCommandState": [("Fcutoff", 0), ("Fdefa", 0), ("Fgoto", 0)]}
    pooled = typepool.pool(conn, uses, set())["PlayerCommandState"]
    assert [off for off, _w, _t in pooled] == [0x38, 0xC2, 0xC4, 0xFD]
    assert len(pooled) > 2                     # wider than any contributor
    widths = {off: w for off, w, _t in pooled}
    assert widths[0xC2] == 4                   # widest access wins, as structgen


def test_build_declared_types_are_never_pooled():
    """Redefining Gfx or OSMesgQueue would fabricate a rival definition."""
    conn = _Conn({"f": [("param0", 0, 4, 1, 1)]})
    uses = {"OSMesgQueue": [("f", 0)]}
    assert typepool.pool(conn, uses, {"OSMesgQueue"}) == {}


def test_member_offsets_decodes_m2c_hex_names():
    assert typepool.member_offsets(["unk0", "unkC2", "unkFD"]) == {
        "unk0": 0, "unkC2": 0xC2, "unkFD": 0xFD}


def test_member_offsets_ignores_real_names():
    assert typepool.member_offsets(["finePitchOffset", "pdrums"]) == {}


def test_named_fields_places_members_at_their_encoded_offsets():
    fields = [(0x38, 4, "s32"), (0xC2, 2, "s16"), (0xC4, 2, "s16")]
    assert typepool.named_fields(fields, ["unkC2", "unk38"]) == {
        0xC2: "unkC2", 0x38: "unk38"}


def test_named_fields_declines_when_a_member_has_a_real_name():
    """A real name carries no offset; guessing one is the fabrication this
    project exists to prevent."""
    fields = [(0x38, 4, "s32")]
    assert typepool.named_fields(fields, ["finePitchOffset"]) is None


def test_named_fields_declines_when_the_pool_lacks_the_offset():
    """Draft and pool disagree; neither should be trusted."""
    fields = [(0x38, 4, "s32")]
    assert typepool.named_fields(fields, ["unkC2"]) is None


def test_type_uses_finds_pointer_parameters(tmp_path):
    ws = tmp_path / "nonmatchings" / "Fcutoff"
    ws.mkdir(parents=True)
    (ws / "base.c").write_text(
        '#include "common.h"\n\n'
        "s32 Fcutoff(PlayerCommandState *arg0, u8 *arg1) {\n"
        "    arg0->unkC2 = 0;\n}\n", encoding="utf-8")
    uses = typepool.type_uses(tmp_path, ["Fcutoff"])
    assert uses["PlayerCommandState"] == [("Fcutoff", 0)]
    assert uses["u8"] == [("Fcutoff", 1)]


def test_type_uses_skips_functions_with_no_draft(tmp_path):
    (tmp_path / "nonmatchings" / "missing").mkdir(parents=True)
    assert typepool.type_uses(tmp_path, ["missing"]) == {}

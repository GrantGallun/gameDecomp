"""m2c unaligned copy loop -> alignment-1 struct assignment (solver.unaligned_copy)."""
from pathlib import Path

from solver import unaligned_copy

FIX = Path(__file__).parent / "fixtures"
SRC = (FIX / "unaligned_copy_osMotorStart.c").read_text()
DIFF = (FIX / "unaligned_copy_osMotorStart.diff").read_text()


def test_fires_on_motivating_residual():
    # osMotorStart best campaign attempt: the target copies 40 bytes with lwl/lwr + sw; the candidate
    # packs bytes in m2c's transcription of that loop.
    out = list(unaligned_copy.variants(SRC, "osMotorStart", DIFF))
    assert [label for label, _ in out] == ["unaligned_copy:loop:sp1C:40"]
    cand = out[0][1]
    assert "typedef struct { u8 b0; u8 b1;" in cand and "u8 b39; } Unaligned40;" in cand
    assert "Unaligned40 sp1C;" in cand and "u32 sp1C[10];" not in cand
    assert "sp1C = *(Unaligned40 *)(sp44);" in cand
    assert "<< 24" not in cand                      # every packed word read is gone
    assert "sp1C.b38" in cand and "sp1C.b2" in cand   # constant byte indexes become named fields


def test_declines_without_the_target_signature():
    no_lwl = "\n".join(l for l in DIFF.splitlines() if not l[1:].strip().startswith(("lwl", "lwr")))
    assert list(unaligned_copy.variants(SRC, "osMotorStart", no_lwl)) == []


INIT_SRC = (FIX / "unaligned_copy_osContGetInitData.c").read_text()
INIT_DIFF = (FIX / "unaligned_copy_osContGetInitData.diff").read_text()


def test_fires_on_straight_line_copy_into_a_struct_local():
    # __osContGetInitData: two lwl/lwr + sw word copies (8 bytes) into a local already typed with the
    # SDK's __OSContRequesFormat; the type is kept, the two packed stores become one assignment.
    out = list(unaligned_copy.variants(INIT_SRC, "__osContGetInitData", INIT_DIFF))
    assert [label for label, _ in out] == ["unaligned_copy:straight:spC:8"]
    cand = out[0][1]
    assert "spC = *(__OSContRequesFormat *)(sp14);" in cand
    assert "<< 24" not in cand and "Unaligned8" not in cand


def test_site_search_offers_it_as_a_shape_edit():
    from solver import site_edits
    edits, receipt = site_edits.propose(SRC, "osMotorStart", DIFF, None)
    assert any(e.kind == "shape:unaligned_copy" for e in edits)


def test_rewrite_drops_the_copy_loops_dead_locals():
    # osMotorStart at -O1: the loop's cursors left declared kept four frame words (0x50 -> 0x70).
    cand = list(unaligned_copy.variants(SRC, "osMotorStart", DIFF))[0][1]
    body = cand[cand.find("osMotorStart("):]
    for dead in ("var_t0", "var_t8", "temp_t1", "temp_at"):
        assert dead not in body
    assert "s32 temp_t4;" in body and "s32 temp_t5;" in body     # still used: kept

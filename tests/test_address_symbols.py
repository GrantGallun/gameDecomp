"""address_symbols fires on the 2026-09-14 census residuals and leaves hardware registers alone."""
from solver import address_symbols as a

TRAINING = """void finishTrainingCourse(void) {
    loadCompressedRomAsset((void *)0x593D10, (void *)0x598A70, 0x29);
}
"""
TRAINING_DIFF = """--- target
+++ candidate
@@ -10,6 +10,6 @@
 lw    ra,0x14(sp)
-lui    a0,%hi(D_593D10)
-lui    a1,%hi(D_598A70)
-addiu    a1,a1,%lo(D_598A70)
-addiu    a0,a0,%lo(D_593D10)
+lui    a0,0x59
+lui    a1,0x59
+ori    a1,a1,0x8a70
+ori    a0,a0,0x3d10
 jal    loadCompressedRomAsset
"""

NAMED = """void updateControllerPakReplaySaveMessageFirstPageFadeOut(void) {
    loadCompressedRomAsset((void *)0x5E0E40, (void *)0x5E26E0, 0x26);
}
"""
NAMED_DIFF = """@@ -23,6 +23,6 @@
 nop
-lui    a0,%hi(gControllerPakReplaySaveMessageSecondPageStart)
-lui    a1,%hi(gControllerPakReplaySaveMessageFirstPageStart)
-addiu    a1,a1,%lo(gControllerPakReplaySaveMessageFirstPageStart)
-addiu    a0,a0,%lo(gControllerPakReplaySaveMessageSecondPageStart)
+lui    a0,0x5e
+lui    a1,0x5e
+ori    a1,a1,0x26e0
+ori    a0,a0,0xe40
 jal    loadCompressedRomAsset
"""

HARDWARE = """s32 __osSiRawStartDma(s32 arg0, void *arg1) {
    (*(volatile u32 *)0xA4800000u) = osVirtualToPhysical(arg1);
    return 0;
}
"""
HARDWARE_DIFF = """@@ -1,3 +1,3 @@
-lui    t7,%hi(SI_DRAM_ADDR_REG)
-sw    v0,%lo(SI_DRAM_ADDR_REG)(t7)
+lui    t7,0xa480
+sw    v0,0(t7)
"""


def test_rom_asset_literals_become_named_address_symbols():
    rows = {label: v for label, _k, v in a.variants(TRAINING, "finishTrainingCourse", TRAINING_DIFF)}
    assert "loadCompressedRomAsset((void *)&D_593D10, (void *)&D_598A70, 0x29);" in rows["address_symbols"]
    assert "extern u8 D_593D10;\nextern u8 D_598A70;\nvoid finishTrainingCourse(void) {" in rows["address_symbols:extern"]


def test_named_symbol_addresses_come_from_lui_ori_pairs():
    assert a.addresses(NAMED_DIFF) == {"gControllerPakReplaySaveMessageSecondPageStart": 0x5E0E40,
                                       "gControllerPakReplaySaveMessageFirstPageStart": 0x5E26E0}
    rows = {label: v for label, _k, v in a.variants(NAMED, "updateControllerPakReplaySaveMessageFirstPageFadeOut", NAMED_DIFF)}
    assert "(void *)&gControllerPakReplaySaveMessageSecondPageStart, (void *)&gControllerPakReplaySaveMessageFirstPageStart" \
        in rows["address_symbols"]


def test_signed_low_offsets_and_dereferenced_hardware_registers():
    assert a.addresses(HARDWARE_DIFF) == {"SI_DRAM_ADDR_REG": 0xA4800000}
    assert a.addresses("@@\n-lui a0,%hi(X)\n-addiu a0,a0,%lo(X)\n+lui a0,0x8016\n+addiu a0,a0,-0x10\n") == {"X": 0x8015FFF0}
    # The dereference already links to identical bytes; rewriting it is not this generator's job.
    assert list(a.variants(HARDWARE, "__osSiRawStartDma", HARDWARE_DIFF)) == []


LABELS = """void drawTrickAttackChallengeLabels(void *arg0) {
    /* "not a literal" */
    drawMenuAsciiTextDefaultScale(0x60, -0x61, "Point", 5);
    drawMenuAsciiTextDefaultScale(0x38, 0x47, "Time Limit", 5);
}
"""
LABELS_DIFF = """@@ -3,9 +3,9 @@
-lui    a2,%hi(D_800E1730)
-addiu    a2,a2,%lo(D_800E1730)
+lui    a2,%hi(.rodata)
+addiu    a2,a2,%lo(.rodata)
 jal    drawMenuAsciiTextDefaultScale
 li    a3,5
-lui    a2,%hi(D_800E1738)
-addiu    a2,a2,%lo(D_800E1738)
+lui    a2,%hi(.rodata)
+addiu    a2,a2,%lo(.rodata+8)
"""


def test_named_rodata_replaces_literals_at_the_observed_pool_offsets():
    assert a.rodata_names(LABELS_DIFF) == {0: "D_800E1730", 8: "D_800E1738"}
    rows = {label: v for label, _k, v in a.variants(LABELS, "drawTrickAttackChallengeLabels", LABELS_DIFF)}
    fixed = rows["named_rodata:extern"]
    assert fixed.startswith("extern char D_800E1730[];\nextern char D_800E1738[];\nvoid drawTrickAttackChallengeLabels")
    assert "(0x60, -0x61, D_800E1730, 5)" in fixed and "(0x38, 0x47, D_800E1738, 5)" in fixed
    assert '"not a literal"' in fixed
    assert "extern char" not in rows["named_rodata"]


def test_named_rodata_declines_when_pool_offsets_disagree():
    # "Point" occupies offsets 0..7; a site at 4 cannot be a literal start.
    wrong = LABELS_DIFF.replace("%lo(.rodata+8)", "%lo(.rodata+4)")
    assert not list(a.named_rodata(LABELS, "drawTrickAttackChallengeLabels", wrong))
    # Float loads are not address-taken sites; schema 3 certifies those literals as they are.
    floats = "@@ -1,2 +1,2 @@\n-lwc1    $f4,%lo(D_800E0A50)(at)\n+lwc1    $f4,%lo(.rodata)(at)\n"
    assert a.rodata_names(floats) == {}


def test_segment_bounds_replace_splat_names_the_real_link_does_not_define():
    segments = a.segments_from({"segments": [{"start": 0x593D10, "name": "_593D10"}, {"start": 0x598A70, "name": "_598A70"},
                                             {"start": 0x60F1A0, "name": "_60F1A0"}, [0x60F990, "bin", "_60F990"], [0x610000]]})
    rows = {label: v for label, _k, v in a.variants(TRAINING, "finishTrainingCourse", TRAINING_DIFF, segments=segments)}
    fixed = rows["address_symbols:segments"]
    assert "loadCompressedRomAsset((void *)&_593D10_ROM_START, (void *)&_593D10_ROM_END, 0x29);" in fixed
    assert fixed.startswith('#include "assets.h"\nvoid finishTrainingCourse(void) {')
    # A lone end address with no preceding start is spelled as the segment that ends there.
    assert a._link_name(0x598A70, None, segments) == "_598A70_ROM_START"
    assert a._link_name(0x610000, None, segments) == "_60F990_ROM_END"

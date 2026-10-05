"""The residual gate must FIRE on a sample that has the real fault and decline everything else.

Each decline test pins a bug this gate actually had while being calibrated on real residuals.
"""
from tools import residual_gate as g


def _bg(*functions: list[str]) -> g.Background:
    return g.document_frequency([list(f) for f in functions])


# a background in which ordinary arithmetic is everywhere and `or R,R,R` is rare
COMMON = ["addiu R,R,#", "sll R,R,#", "andi R,R,#", "lw R,#(sp)", "jr ra", "nop", "addu R,R,R"]
BACKGROUND = _bg(*([COMMON] * 40))


# verbatim first hunk of attempt 2926 (calculateRaceTimerDelta): a register-only difference
REGISTER_ONLY = """\
--- target_object_dump_normalized.s
+++ candidate_object_dump_normalized.s
@@ -15,7 +15,7 @@
 lb    t9,0(a1)
 multu    t2,t0
 mflo    t3
-addu    v1,t1,t3
+addu    v0,t1,t3
 nop
 multu    t6,a3
 mflo    t8
"""


def test_register_only_difference_is_not_a_shape_fault():
    """Counting every `-` line read 2926's register choices as three division-trap faults."""
    verdict = g.check(REGISTER_ONLY, ["   0:\taddu\tv0,t1,t3"], BACKGROUND)
    assert not verdict.reproduces
    assert verdict.components == ()
    assert verdict.register_only_lines == 1
    assert "register/immediate only" in verdict.reason


# An RGBA5551 packing residual in the instruction order of drawMenuSolidRect's real target
# (`ori; sll; or; andi`), where the candidate ADDs instead of ORs. A first fixture written from
# memory as `sll; andi; or` matched neither the target nor any sample -- the gate was right.
COLOR = """\
@@ -1,6 +1,6 @@
 ori    t7,t4,0x1
 sll    t9,t7,0x10
-or    t6,t9,t7
+addu    t6,t9,t7
 andi    t8,t6,0x3e
 sra    t9,t8,0x2
"""

# verbatim listing of synthetic color_pack seed 30, compiled by the SBK1 recipe
COLOR_SAMPLE = """\
   0:	addiu	sp,sp,-24
   4:	lh	t0,42(sp)
   8:	sw	a2,32(sp)
   c:	move	t8,a2
  10:	sll	t3,a3,0x3
  14:	sll	t1,t0,0x8
  18:	move	a2,t8
  1c:	andi	t2,t1,0xf800
  20:	andi	t4,t3,0x7c0
  24:	sra	t7,a1,0x2
  28:	andi	t8,t7,0x3e
  2c:	or	t5,t2,t4
  30:	sw	ra,20(sp)
  34:	sw	a0,24(sp)
  38:	sw	a1,28(sp)
  3c:	sll	t6,a0,0x10
  40:	or	t9,t5,t8
  44:	andi	t0,a2,0x1
  48:	sra	a0,t6,0x10
  4c:	jal	0 <syn_color_pack_30>
  50:	or	a1,t9,t0
""".splitlines()

SWITCH_SAMPLE = """\
   0:	sltiu	at,a0,8
   4:	beqz	at,68 <syn_switch+0x68>
   8:	li	v0,-1
   c:	sll	t6,a0,0x2
  10:	lui	at,0x0
  14:	addu	at,at,t6
  18:	lw	t6,0(at)
  20:	jr	t6
  28:	jr	ra
""".splitlines()


def test_fires_when_the_sample_contains_the_rare_fault():
    """The motivating case: fabricated data that exercises the real residual's own shape."""
    verdict = g.check(COLOR, COLOR_SAMPLE, BACKGROUND)
    assert verdict.reproduces, verdict
    (component,) = verdict.reproduced
    assert "or R,R,R" in component.shapes
    assert component.anchors_shared


def test_declines_a_sample_without_the_fault():
    verdict = g.check(COLOR, SWITCH_SAMPLE, BACKGROUND)
    assert not verdict.reproduces
    assert [c.status for c in verdict.components] == ["not-reproduced"]


def test_rarity_belongs_to_the_fault_not_its_context():
    """Flength's fault was one common `addiu`, rare only through its neighbours; dense-switch
    samples 'covered' 18 residuals like it and 0 of the 18 had a jump table."""
    diff = """\
@@ -1,5 +1,5 @@
 or    t6,t9,t8
 or    t5,t6,t7
-addiu    t4,t5,1
+addu    t4,t5,t3
 or    t3,t4,t2
"""
    rare_context_sample = ["   0:\tor\tt6,t9,t8", "   4:\tor\tt5,t6,t7", "   8:\taddiu\tt4,t5,1",
                           "   c:\tor\tt3,t4,t2"]
    verdict = g.check(diff, rare_context_sample, BACKGROUND)
    assert not verdict.reproduces
    assert all(c.status == "unshaped" for c in verdict.components)


def test_each_fault_in_a_residual_is_judged_separately():
    """One sample should not have to contain every fault in someone else's function."""
    diff = """\
@@ -1,12 +1,12 @@
 sll    t9,t7,0x10
-or    t6,t9,t8
+addu    t6,t9,t8
 andi    t8,t9,0x3e
 lw    t1,0(sp)
 addiu    t1,t1,1
 lw    t2,4(sp)
 addiu    t2,t2,1
-div    zero,t1,t2
+addu    t3,t1,t2
 nop
"""
    background = _bg(*([COMMON] * 40))
    parts = g.components(diff, background)
    assert len(parts) == 2
    verdict = g.check(diff, COLOR_SAMPLE, background)
    assert [c.status for c in verdict.components] == ["reproduced", "not-reproduced"]


def test_ra_is_structural_only_as_the_return_address():
    """In a leaf IDO allocates `ra` as scratch; drawMenuTextureByAssetId's allocation residual
    showed up as five 'rare' faults while `ra` was kept literal."""
    assert g.shape("addu", "ra,t6,t7") == g.shape("addu", "t9,t6,t7")
    assert g.shape("slt", "at,ra,t0") == "slt at,R,R"
    assert g.shape("jr", "ra") == "jr ra"
    assert g.shape("sw", "ra,20(sp)") == "sw ra,#(sp)"


def test_fpu_registers_and_relocated_constants_have_stable_shapes():
    assert g.shape("lwc1", "$f4,%lo(D_800E1AE0)(at)") == "lwc1 F,#(at)"
    assert g.shape("mul.d", "$f8,$f4,$f6") == "mul.d F,F,F"
    assert g.shape("break", "0x7") != g.shape("break", "0x6")


def test_objdump_and_normalized_formats_agree():
    """Samples come from objdump, residuals from the oracle's normalized dump."""
    objdump = g.listing_shapes(["  14:\tli\tat,-1", "  18:\tbne\ta2,at,2c <f+0x2c>"])
    normalized = g.normalized_shapes(["li    at,-0x1", "bne    a3,at,b0"])
    assert objdump == normalized


def test_coverage_reports_gaps_and_register_only_residuals():
    samples = [("color_pack", g.sample_grams(COLOR_SAMPLE)), ("switch", g.sample_grams(SWITCH_SAMPLE))]
    report = g.coverage({"a_color": COLOR, "b_register": REGISTER_ONLY}, samples, BACKGROUND)
    assert report["register_only_residuals"] == 1
    assert report["covered_components"] == 1
    assert report["hits"]["color_pack"] == ["a_color"]
    assert report["hits"]["switch"] == []

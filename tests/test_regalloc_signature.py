"""Signatures fire on their motivating residuals (2026-09-13 campaign cohort)."""
from solver import regalloc_signature as rs

TARGET_MULT = """\
sra    t8,at,0xc
multu    a0,v0
sh    t8,6(s0)
bgez    t9,a0
multu    v1,v0
"""


def test_commutative_swap_fires_on_makeFixedRotationXY():
    candidate = TARGET_MULT.replace("multu    a0,v0", "multu    v0,a0").replace("multu    v1,v0", "multu    v0,v1")
    report = rs.compare(TARGET_MULT, candidate)
    assert report.signatures == {"commutative_swap": 2}
    assert report.gradient == (0, 2, 4)
    # The branch target "a0" is an offset, never a register.
    assert rs.parse("bgez    t9,a0")[0].registers() == [(0, "t9")]


def test_temp_vs_variable_fires_on_randomNextObject():
    target = "lbu    t6,0x518(a0)\naddiu    t7,t6,1\n"
    candidate = "lbu    v1,0x518(a0)\naddiu    t7,v1,1\n"
    report = rs.compare(target, candidate)
    assert report.signatures == {"temp_vs_variable": 2}
    assert report.substitutions == {("t6", "v1"): 2}


def test_other_classes_and_memory_bases():
    report = rs.compare("lw    a0,4(s0)\naddu    t6,t7,t8\nmove    a2,a1\n",
                        "lw    a0,4(s1)\naddu    t7,t7,t8\nmove    a3,a1\n")
    assert report.signatures == {"saved_order": 1, "temp_numbering": 1, "variable_colour": 1}


def test_non_register_differences_dominate_the_gradient_and_delta_reports_progress():
    target = "addiu    sp,sp,-0x18\nmultu    a0,v0\njr    ra\n"
    extra = "addiu    sp,sp,-0x18\nmove    a1,a0\nmultu    v0,a0\njr    ra\n"
    swapped = "addiu    sp,sp,-0x18\nmultu    v0,a0\njr    ra\n"
    worse, better = rs.compare(target, extra), rs.compare(target, swapped)
    assert worse.gradient[0] == 1 and better.gradient == (0, 1, 2)
    assert rs.delta(worse, better)["verdict"] == "better"
    assert rs.delta(better, rs.compare(target, target)) == {
        "verdict": "exact_shape", "gradient": [[0, 1, 2], [0, 0, 0]], "fixed": {"commutative_swap": 1}, "introduced": {}}

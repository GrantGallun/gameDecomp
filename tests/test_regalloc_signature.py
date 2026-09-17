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


# --- reordered vs renames: splitting the register count into its two causes (2026-09-17) ---

# The whole residual of the five 99.936 siblings, copied from their recorded diffs. signals.analyse
# books it ordering=2/regalloc=0; the positional pairing here books it saved_order=2. Both are
# consistent with the bytes, because both instructions write the constant zero.
SIBLING_RESIDUAL = ("slti    at,s0,0x10\n"
                    "bnez    at,3c\n"
                    "addiu    s2,s2,2\n"
                    "move    s2,zero\n"
                    "move    s3,zero\n"
                    "li    s0,0x80\n")


def test_a_permuted_pair_is_not_counted_as_a_rename():
    """The motivating residual: same two instructions, other order.

    `gradient` and `signatures` are deliberately unchanged -- that pairing is what the search
    ranks on and moving it would change behaviour. What the new counters add is the arithmetic
    that says the difference is positional.
    """
    candidate = SIBLING_RESIDUAL.replace("move    s2,zero\nmove    s3,zero\n",
                                         "move    s3,zero\nmove    s2,zero\n")
    report = rs.compare(SIBLING_RESIDUAL, candidate)
    assert report.gradient == (0, 2, 2)               # unchanged: the search sees what it saw
    assert report.signatures == {"saved_order": 2}     # unchanged
    assert (report.reordered, report.renames) == (2, 0)
    assert report.rename_signatures == {}              # nothing is left once text is matched first
    assert report.order_only is True


def test_a_real_rename_survives_the_reordering_pairing():
    """`move s2,zero` against `move t6,zero`: no identical instruction on the other side."""
    report = rs.compare("move    s2,zero\n", "move    t6,zero\n")
    assert (report.reordered, report.renames) == (0, 1)
    assert report.rename_signatures == {"temp_vs_variable": 1}
    assert report.order_only is False


def test_one_moved_and_one_renamed_inside_the_same_run_are_counted_separately():
    """Two independent causes in one shape-equal block, and neither hides the other."""
    target = "move    s2,zero\nmove    s3,zero\n"
    candidate = "move    s3,zero\nmove    t6,zero\n"
    report = rs.compare(target, candidate)
    assert (report.reordered, report.renames) == (1, 1)
    assert report.rename_signatures == {"temp_vs_variable": 1}
    assert report.order_only is False


def test_the_two_counters_partition_the_register_count():
    """`reordered + renames == register_instructions` on every shape, so the split is auditable."""
    pairs = [(SIBLING_RESIDUAL, SIBLING_RESIDUAL.replace("move    s2,zero\nmove    s3,zero\n",
                                                         "move    s3,zero\nmove    s2,zero\n")),
             (TARGET_MULT, TARGET_MULT.replace("multu    a0,v0", "multu    v0,a0")
                                      .replace("multu    v1,v0", "multu    v0,v1")),
             ("lbu    t6,0x518(a0)\naddiu    t7,t6,1\n", "lbu    v1,0x518(a0)\naddiu    t7,v1,1\n"),
             ("lw    a0,4(s0)\naddu    t6,t7,t8\nmove    a2,a1\n",
              "lw    a0,4(s1)\naddu    t7,t7,t8\nmove    a3,a1\n"),
             ("addiu    sp,sp,-0x18\nmultu    a0,v0\njr    ra\n",
              "addiu    sp,sp,-0x18\nmove    a1,a0\nmultu    v0,a0\njr    ra\n")]
    for target, candidate in pairs:
        report = rs.compare(target, candidate)
        total = report.reordered + report.renames
        assert total == report.register_instructions, (target, candidate, report.to_dict())
    assert rs.compare("lw    a0,0(a1)\n", "lw    a0,0(a1)\n").order_only is False

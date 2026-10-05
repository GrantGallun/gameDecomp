"""m2c copy-back temporaries merged into their variable (solver.temp_copyback)."""
from solver import temp_copyback

HEAVY = "\n".join(["--- t", "+++ c", "@@ -1,2 +1,4 @@", " addiu sp,sp,-0x20", "+sw t8,0x18(sp)", "+lw t1,0x18(sp)", " jr ra"])

LOOP = """s32 f(s32 n, u8 *p) {
    s32 sp4C;
    s32 temp_t5;

    sp4C = 0;
    for (;;) {
        temp_t5 = sp4C + 1;
        sp4C = temp_t5;
        p += 1;
        if (!(temp_t5 < n)) break;
    }
    return (s32)p;
}
"""


def test_merges_the_loop_counter_copy_back():
    # osMotorStart after the unaligned-copy repair: `temp_t5 = sp4C + 1; sp4C = temp_t5;` homed at -O1.
    out = list(temp_copyback.variants(LOOP, "f", HEAVY))
    assert [label for label, _ in out] == ["temp_copyback:temp_t5->sp4C"]
    cand = out[0][1]
    assert "sp4C = sp4C + 1;" in cand and "if (!(sp4C < n)) break;" in cand
    assert "temp_t5" not in cand


def test_declines_when_the_temporary_is_read_before_the_pair():
    src = LOOP.replace("        p += 1;\n", "        p += temp_t5;\n").replace(
        "    for (;;) {\n", "    for (;;) {\n        p += temp_t5;\n", 1)
    assert list(temp_copyback.variants(src, "f", HEAVY)) == []


def test_declines_when_a_read_follows_a_reassignment():
    src = LOOP.replace("        p += 1;\n", "        sp4C = 7;\n")
    assert list(temp_copyback.variants(src, "f", HEAVY)) == []


def test_gated_on_extra_stack_traffic():
    light = "\n".join(["--- t", "+++ c", "@@ -1,2 +1,2 @@", "-addiu v0,v0,1", "+addiu v0,v0,2", " jr ra"])
    assert list(temp_copyback.variants(LOOP, "f", light)) == []

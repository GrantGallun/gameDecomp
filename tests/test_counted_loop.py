"""m2c rotated counted loop -> for loop (solver.counted_loop)."""
from solver import counted_loop

DRAFT = """s32 f(OSPfs *arg0) {
    s32 sp4C;
    u8 *sp44;
    s32 temp_t5;

    sp44 = (u8 *)&__osPfsPifRam;
    if (arg0->channel != 0) {
        sp4C = 0;
        if (arg0->channel > 0) {
            for (;;) {
                temp_t5 = sp4C + 1;
                sp4C = temp_t5;
                sp44 += 1;
            
                if (!(temp_t5 < arg0->channel)) break;
            }
        }
    }
    return (s32)sp44;
}
"""


def test_restores_osMotorStarts_loop_from_the_raw_draft():
    # The campaign's own draft (temp form): the `for` spelling is what made osMotorStart exact.
    out = list(counted_loop.variants(DRAFT, "f"))
    assert [label for label, _ in out] == ["counted_loop:sp4C"]
    cand = out[0][1]
    assert "for (sp4C = 0; sp4C < arg0->channel; sp4C++) {\n            sp44 += 1;\n        }" in cand
    assert "for (;;)" not in cand and "temp_t5" not in cand
    assert "if (arg0->channel != 0) {" in cand


def test_increment_at_the_end_after_reads_is_a_for_body():
    src = """void g(s32 n) {
    s32 i;

    i = 0;
    if (n > 0) {
        for (;;) {
            if (i != 3) {
                h(i);
            }
            i += 1;

            if (!(i < n)) break;
        }
    }
}
"""
    cand = list(counted_loop.variants(src, "g"))[0][1]
    assert "for (i = 0; i < n; i++) {" in cand and "h(i);" in cand and "i += 1;" not in cand


def test_declines_when_something_after_the_increment_reads_the_counter():
    src = DRAFT.replace("                sp44 += 1;\n", "                sp44 += sp4C;\n")
    assert list(counted_loop.variants(src, "f")) == []

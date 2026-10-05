"""strength_inverse.counter_loop_variants (H6): fires on the 99.936 siblings' shape, declines where it must."""
from solver import strength_inverse

# drawRaceSplitscreenSelectOption2Frame's search source, reduced: a parallel counter in the first loop, derived
# variables stepped at the tail of a `for (;;)` in the second, and steering residue (`if ((1))`, `i++; i--;`).
SIB = """void drawRaceSplitscreenSelectOption2Frame(Actor *arg0) {
    s32 i;
    s32 tileIndex;
    s32 offset;

    tileIndex = 0;
    for (i = 0; i < 16; i++, tileIndex++) {
        draw(arg0->x + ((i & 3) << 5), tiles.center[tileIndex]);
    }

    if ((1)) {
        tileIndex = 0;
        i = 0x80;
    }
    offset = 0;
    for (;;) {
        draw(arg0->x + 0x80, arg0->y + offset, tiles.right[tileIndex]);
        draw(arg0->x + offset, tiles.bottom[tileIndex]);
        i = 0x80;
        offset += 0x40;
        tileIndex++;

        if (!(offset != i)) break;
    }
    i++;
    i--;

    draw(arg0->x + 0x80, tiles.corner);
}
"""


def test_counter_loop_fires_on_the_siblings_and_combines_both_rewrites_first():
    out = list(strength_inverse.counter_loop_variants(SIB, "drawRaceSplitscreenSelectOption2Frame"))
    label, cand = out[0]
    assert label == "counter_loop:offset->i+parallel"
    assert "    for (i = 0; i < 16; i++) {\n        draw(arg0->x + ((i & 3) << 5), tiles.center[i]);\n" in cand
    assert ("    for (i = 0; i < 2; i++) {\n        draw(arg0->x + 0x80, arg0->y + i * 0x40, tiles.right[i]);\n"
            "        draw(arg0->x + i * 0x40, tiles.bottom[i]);\n    }\n") in cand
    assert "i++;\n    i--;" not in cand and "if (!(offset" not in cand


def test_counter_loop_declines_when_a_derived_variable_is_read_after_the_loop():
    used = SIB.replace("    draw(arg0->x + 0x80, tiles.corner);", "    draw(arg0->x + offset, tiles.corner);")
    labels = [label for label, _ in strength_inverse.counter_loop_variants(used, "drawRaceSplitscreenSelectOption2Frame")]
    assert not any(label.startswith("counter_loop:offset->") for label in labels)


def test_counter_loop_declines_an_inexact_trip_count():
    odd = SIB.replace("offset += 0x40;", "offset += 0x30;")
    labels = [label for label, _ in strength_inverse.counter_loop_variants(odd, "drawRaceSplitscreenSelectOption2Frame")]
    assert not any(label.startswith("counter_loop:offset->") for label in labels)


def test_search_stream_includes_counter_loop():
    from solver import regalloc_mutations
    kinds = {kind for _l, kind, _c in regalloc_mutations.variants(SIB, "drawRaceSplitscreenSelectOption2Frame", "")}
    assert "counter_loop" in kinds

"""Recorded-offset member repair FIRES on its motivating residual and declines otherwise.

Motivating residual (initRaceUiBurstTextParticle, 2026-09-13): the game passes
image = a0+0x38+4n and palette = a0+0x28+4n to getAssetTableImageAndPalette,
while the candidate's generated record interleaves image_n/palette_n from 0x28.
gpt-oss:20b swapped arguments or reordered the record but never completed the
permutation in 16 calls.
"""
from solver import recorded_layout

ROWS = [{"member": name, "offset": 0x28 + 4 * i, "width": 4, "canonical": "void *"}
        for i, name in enumerate(["image0", "palette0", "image1", "palette1",
                                  "image2", "palette2", "image3", "palette3"])]
FIELDS = {"a0": {"parameter": "arg0", "record": "struct Actor", "rows": ROWS}}
SOURCE = """struct Actor { char pad[0x28]; void *image0; void *palette0; void *image1; void *palette1;
    void *image2; void *palette2; void *image3; void *palette3; };
void f(struct Actor *arg0) {
    if (arg0->pad[0] == 0) {
        load(0x27, &arg0->image0, &arg0->palette0);
        load(0x28, &arg0->image1, &arg0->palette1);
        load(0x29, &arg0->image2, &arg0->palette2);
        load(0x2A, &arg0->image3, &arg0->palette3);
    } else {
        load(0x2B, &arg0->image0, &arg0->palette0);
    }
}
"""


def constraint(candidate, target):
    return {"kind": "argument", "where": "call", "register": "a0",
            "candidate_offset": candidate, "target_offset": target}


def recorded():
    """What the replay reports for the four executed calls."""
    rows = []
    for n in range(4):
        rows.append(constraint(0x28 + 8 * n, 0x38 + 4 * n))       # image argument
        rows.append(constraint(0x2C + 8 * n, 0x28 + 4 * n))       # palette argument
    return rows


def test_fires_on_the_interleaved_image_palette_layout():
    repaired, report = recorded_layout.propose(SOURCE, "f", recorded(), FIELDS)
    assert report["status"] == "proposed" and report["declines"] == []
    # Every call now names the member at the offset the game used.
    assert "load(0x27, &arg0->image2, &arg0->image0);" in repaired
    assert "load(0x28, &arg0->palette2, &arg0->palette0);" in repaired
    assert "load(0x29, &arg0->image3, &arg0->image1);" in repaired
    assert "load(0x2A, &arg0->palette3, &arg0->palette1);" in repaired
    # The unrecorded branch uses the same members and gets the same rename.
    assert "load(0x2B, &arg0->image2, &arg0->image0);" in repaired
    # Declarations are untouched: only body uses are renamed.
    assert repaired.split("void f(")[0] == SOURCE.split("void f(")[0]


def test_declines_conflicts_collisions_and_non_members():
    conflicting = [constraint(0x28, 0x38), constraint(0x28, 0x3C)]
    assert recorded_layout.propose(SOURCE, "f", conflicting, FIELDS)[0] is None
    colliding = [constraint(0x28, 0x38), constraint(0x2C, 0x38)]
    _source, report = recorded_layout.propose(SOURCE, "f", colliding, FIELDS)
    assert _source is None and "would all become" in report["declines"][0]
    inside = [constraint(0x29, 0x38)]
    assert "not the start" in recorded_layout.propose(SOURCE, "f", inside, FIELDS)[1]["declines"][0]
    unmeasured = [{**constraint(0x28, 0x38), "register": "a1"}]
    assert "not a measured" in recorded_layout.propose(SOURCE, "f", unmeasured, FIELDS)[1]["declines"][0]


def test_declines_different_types_and_leaves_nested_paths_alone():
    rows = [{"member": "a", "offset": 0, "width": 4, "canonical": "int"},
            {"member": "b", "offset": 4, "width": 4, "canonical": "float"},
            {"member": "pos", "offset": 8, "width": 4, "canonical": "struct V"},
            {"member": "pos.x", "offset": 8, "width": 4, "canonical": "int"},
            {"member": "c", "offset": 0xC, "width": 4, "canonical": "int"}]
    fields = {"a0": {"parameter": "p", "record": "S", "rows": rows}}
    source = "void g(S *p) {\n    use(p->a, p->pos.x, p->c);\n}\n"
    typed = [{**constraint(0, 4)}]
    assert "differ in width or type" in recorded_layout.propose(source, "g", typed, fields)[1]["declines"][0]
    repaired, report = recorded_layout.propose(source, "g", [constraint(0xC, 8)], fields)
    assert report["renames"] == {"p->c": "pos.x"}
    assert "use(p->a, p->pos.x, p->pos.x);" in repaired

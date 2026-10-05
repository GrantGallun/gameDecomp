from solver import line_map

DUMP = """
Disassembly of section .text:

00000000 <other>:
other.c:2
   0:	03e00008 	jr	ra
   4:	00000000 	nop

00000008 <f>:
f.c:11
   8:	8c820004 	lw	v0,4(a0)
f.c:12
   c:	24420001 	addiu	v0,v0,1
  10:	03e00008 	jr	ra
f.c:13
  14:	ac820008 	sw	v0,8(a0)
"""
LISTING = ["lw    v0,0x4(a0)", "addiu    v0,v0,0x1", "jr    ra", "sw    v0,0x8(a0)"]


def test_rows_map_to_lines_by_position_with_offset():
    lm = line_map.build(DUMP, "f", LISTING, line_offset=10)
    assert [lm.line_of(i) for i in range(4)] == [1, 2, 2, 3]
    assert lm.rows_of(2) == [1, 2] and lm.line_of(99) is None


def test_attribute_names_changed_lines_and_missing_gaps():
    lm = line_map.build(DUMP, "f", LISTING, line_offset=10)
    target = ["lw    v0,0x4(a0)", "addiu    v0,v0,0x2", "jr    ra", "sw    v0,0x8(a0)"]
    got = lm.attribute(target, LISTING)
    assert got["changed"] == {2: [1]} and not got["gaps"]
    missing = ["lw    v0,0x4(a0)", "sll    v0,v0,0x2", "addiu    v0,v0,0x1", "jr    ra", "sw    v0,0x8(a0)"]
    got = lm.attribute(missing, LISTING)
    assert got["gaps"] == [(1, 2)]
    assert "missing between line 1 and line 2" in line_map.render(got, 3)


def test_a_dump_that_does_not_cover_the_listing_is_refused():
    import pytest
    with pytest.raises(ValueError):
        line_map.build(DUMP, "f", LISTING + ["nop", "nop"])


def test_declaration_influence_names_the_declaration_that_moves_differing_rows():
    from tools import context_closure as cc
    ctx = "typedef short s16;\ntypedef int s32;\n"
    fn = cc.render_function(cc.find_funcdef(cc.parse(ctx + "s32 f(s16 *p) { s16 x; s32 y; x = p[1]; y = 3; return x + y; }"), "f"))

    def fake_compile(text):            # the declared type of x decides the load; y's type changes nothing
        return ["lh v0,2(a0)" if "  s16 x;" in text else "lhu v0,2(a0)", "addiu v0,v0,3", "jr ra"]
    influence = line_map.declaration_influence(ctx, fn, "f", fake_compile)
    x_line = fn.split("\n").index("  s16 x;") + 1
    assert influence == {x_line: {0}}
    att = line_map.with_declarations({"changed": {5: [0]}, "gaps": []}, influence)
    assert att["declarations"] == {x_line: [0]}
    assert f"declaration(s) on line(s): {x_line}" in line_map.render(att, 20)

from eval.parent_struct_annotation_pilot import inject_prelude


def test_injects_annotation_before_parent_definition():
    source = '#include "common.h"\n\nvoid updateRacePickupIdle(void *arg0) {\n}\n'

    result = inject_prelude(source, "struct X { int value; };\n")

    assert result.index("struct X") < result.index("void updateRacePickupIdle")

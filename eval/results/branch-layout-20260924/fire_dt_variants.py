"""Variants for the drawTrainingCourseLessonEndMenu fire test: merge m2c's split variables (H5 exploration) and the
select as if/else (H1')."""
import json
from pathlib import Path

SEL1 = ("            var_s3 = 0x60;\n            if (var_s0_3 == arg0->unk24) {\n                var_s3 = 0x100;\n            }\n",
        "            if (var_s0_3 == arg0->unk24) {\n                var_s3 = 0x100;\n            } else {\n                var_s3 = 0x60;\n            }\n")
SEL2 = ("            var_s3_2 = 0x60;\n            if ((var_s0_2 + 1) == arg0->unk24) {\n                var_s3_2 = 0x100;\n            }\n",
        "            if ((var_s0_2 + 1) == arg0->unk24) {\n                var_s3_2 = 0x100;\n            } else {\n                var_s3_2 = 0x60;\n            }\n")


def merge(names, into):
    edits = []
    for n in names:
        edits.append([f"    s32 {n};\n", ""])
    for n in sorted(names, key=len, reverse=True):
        edits.append([n, into])
    return edits


S1 = merge(["var_s1_2"], "var_s1")
ALL = merge(["var_s1_2", "var_s1_3", "var_s1_4"], "var_s1") + merge(["var_s0_2", "var_s0_3", "var_s0_4"], "var_s0") + \
    merge(["var_s3_2"], "var_s3")
v = {
    "select": [list(SEL1), list(SEL2)],
    "merge_s1": S1,
    "merge_all": ALL,
    "select+merge_s1": [list(SEL1), list(SEL2)] + S1,
    "select+merge_all": [list(SEL1), list(SEL2)] + ALL,
}
Path(__file__).with_name("fire_dt.json").write_text(json.dumps(v, indent=1))

S1ALL = merge(["var_s1_2", "var_s1_3", "var_s1_4"], "var_s1")
v2 = {
    "select+s1all": [list(SEL1), list(SEL2)] + S1ALL,
    "select+s1all+s3": [list(SEL1), list(SEL2)] + S1ALL + merge(["var_s3_2"], "var_s3"),
    "select+s1all+s0": [list(SEL1), list(SEL2)] + S1ALL + merge(["var_s0_2", "var_s0_3"], "var_s0"),
    "select+s1all+s0+s3": [list(SEL1), list(SEL2)] + S1ALL + merge(["var_s0_2", "var_s0_3"], "var_s0") + merge(["var_s3_2"], "var_s3"),
}
Path(__file__).with_name("fire_dt2.json").write_text(json.dumps(v2, indent=1))

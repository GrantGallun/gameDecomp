"""Exploration E3 (after H4, before its amendment): which loop forms keep `slt/bnez` as the exit test and which get
`bne` (the `<` -> `!=` rewrite). Constant bound 0x40, step 0x10, as at drawTrainingCourseLessonEndMenu 0x148."""
import probe

H = "extern void g(int);\nextern int gv;\n"
FORMS = {
    "do_s_lt": "    s = 0;\n    do {\n        g(s);\n        s += 16;\n    } while (s < 64);\n",
    "for_s_lt": "    for (s = 0; s < 64; s += 16) {\n        g(s);\n    }\n",
    "for_i_times16": "    for (i = 0; i < 4; i++) {\n        g(i * 16);\n    }\n",
    "for_i_times16_plus": "    for (i = 0; i < 4; i++) {\n        g(gv + i * 16);\n    }\n",
    "goto_s": "    s = 0;\nloop:\n    g(s);\n    s += 16;\n    if (s < 64) goto loop;\n",
    "do_s_ne": "    s = 0;\n    do {\n        g(s);\n        s += 16;\n    } while (s != 64);\n",
    "do_s_lt_used_after": "    s = 0;\n    do {\n        g(s);\n        s += 16;\n    } while (s < 64);\n    g(s);\n",
    "for_i_var_bound": "    for (i = 0; i < gv; i++) {\n        g(i * 16);\n    }\n",
    "nested_inner_do": "    for (i = 0; i < 3; i++) {\n        s = 0;\n        do {\n            g(s + i);\n            s += 16;\n        } while (s < 64);\n    }\n",
}
for name, body in FORMS.items():
    insns = probe.compile_(H + "void f(void) {\n    int s;\n    int i;\n" + body + "}\n", function="f")
    back = [(k, ins, insns[k - 1]) for k, ins in enumerate(insns) if ins.startswith("b") and
            ins.partition(" ")[0] not in ("b", "break") and int(ins.split(",")[-1], 16) // 4 <= k]
    print(f"== {name:22s}", [f"{prev} ; {b}" for _, b, prev in back])

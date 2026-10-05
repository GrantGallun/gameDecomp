"""Exploration E1 (before the protocol): which C form of "pick one of two values in a loop" gives the target's
`li A; bne -> join; nop; b join; li B` shape (drawTrainingCourseLessonEndMenu 0x3cc)."""
import probe

HDR = "extern void syn_use(int, int);\nextern int syn_k;\n"
FORMS = {
    "default_then_override": "        x = 0x60;\n        if (i == syn_k) x = 0x100;\n",
    "if_else": "        if (i == syn_k) x = 0x100; else x = 0x60;\n",
    "if_else_inverted": "        if (i != syn_k) x = 0x60; else x = 0x100;\n",
    "ternary": "        x = (i == syn_k) ? 0x100 : 0x60;\n",
    "ternary_inverted": "        x = (i != syn_k) ? 0x60 : 0x100;\n",
}
for name, body in FORMS.items():
    src = HDR + "void syn_f(void) {\n    int i;\n    int x;\n    for (i = 0; i < 3; i++) {\n" + body + \
        "        syn_use(i, x);\n    }\n}\n"
    insns = probe.compile_(src, function="syn_f")
    print("==", name, probe.branch_shape(insns))
    print("\n".join(f"  {k*4:3x} {s}" for k, s in enumerate(insns)))

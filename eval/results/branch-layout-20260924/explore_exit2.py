"""Exploration E3b: more loop forms with a constant bound, looking for a kept `slti/bnez` exit test."""
import probe

H = "extern void g(int);\nextern void g2(int, int);\nextern int gv;\nextern int arr[];\n"
FORMS = {
    "break_inside": "    for (s = 0; s < 64; s += 16) {\n        if (g2(s, 0), gv) break;\n        g(s);\n    }\n",
    "continue_inside": "    for (s = 0; s < 64; s += 16) {\n        if (gv) continue;\n        g(s);\n    }\n",
    "short_iv": "    short t;\n    for (t = 0; t < 64; t += 16) {\n        g(t);\n    }\n",
    "le_bound": "    for (s = 0; s <= 48; s += 16) {\n        g(s);\n    }\n",
    "inc_mid": "    s = 0;\n    do {\n        s += 16;\n        g(s);\n    } while (s < 64);\n",
    "two_ivs": "    for (s = 0, i = 0; s < 64; s += 16, i++) {\n        g2(s, i);\n    }\n",
    "two_ivs_i_test": "    for (s = 0, i = 0; i < 4; s += 16, i++) {\n        g2(s, i);\n    }\n",
    "iv_in_if": "    for (s = 0; s < 64; s += 16) {\n        if (gv == s) g(s);\n    }\n",
    "array_index": "    for (i = 0; i < 4; i++) {\n        g(arr[i]);\n    }\n",
    "non_multiple": "    for (s = 0; s < 60; s += 16) {\n        g(s);\n    }\n",
    "start_nonzero": "    for (s = 8; s < 64; s += 16) {\n        g(s);\n    }\n",
    "negative_step": "    for (s = 48; s >= 0; s -= 16) {\n        g(s);\n    }\n",
    "while_top": "    s = 0;\n    while (s < 64) {\n        g(s);\n        s += 16;\n    }\n",
    "iv_param_start": "    for (s = p; s < 64; s += 16) {\n        g(s);\n    }\n",
}
for name, body in FORMS.items():
    try:
        insns = probe.compile_(H + "void f(int p) {\n    int s;\n    int i;\n" + body + "}\n", function="f")
    except RuntimeError as e:
        print("==", name, "compile error", str(e)[-200:])
        continue
    back = [(k, ins, insns[k - 1]) for k, ins in enumerate(insns) if ins.startswith("b") and
            ins.partition(" ")[0] not in ("b", "break") and int(ins.split(",")[-1], 16) // 4 <= k]
    print(f"== {name:18s}", [f"{prev} ; {b}" for _, b, prev in back])

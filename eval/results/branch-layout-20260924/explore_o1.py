"""Exploration E2 (before the protocol): at -O1, which C gives a leaf `addiu sp,-8` frame and returns via
`b epilogue` (__osAiDeviceBusy target) rather than per-return `jr ra` (candidate)."""
import probe

HDR = "extern volatile unsigned int syn_reg;\n"
FORMS = {
    "direct": "int syn_f(void) {\n    if (syn_reg & 0x80000000) {\n        return 1;\n    }\n    return 0;\n}\n",
    "if_else_returns": "int syn_f(void) {\n    if (syn_reg & 0x80000000) {\n        return 1;\n    } else {\n        return 0;\n    }\n}\n",
    "local": "int syn_f(void) {\n    unsigned int s = syn_reg;\n    if (s & 0x80000000) {\n        return 1;\n    }\n    return 0;\n}\n",
    "register_local": "int syn_f(void) {\n    register unsigned int s = syn_reg;\n    if (s & 0x80000000) {\n        return 1;\n    } else {\n        return 0;\n    }\n}\n",
    "register_local_signed": "int syn_f(void) {\n    register int s = syn_reg;\n    if (s & 0x80000000) {\n        return 1;\n    } else {\n        return 0;\n    }\n}\n",
}
for opt in ("-O1", "-O2"):
    for name, src in FORMS.items():
        insns = probe.compile_(HDR + src, opt=opt, function="syn_f")
        print("==", opt, name, probe.branch_shape(insns))
        print("\n".join(f"  {k*4:3x} {s}" for k, s in enumerate(insns)))

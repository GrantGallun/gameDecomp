"""Amendment A1 predictions (H1', H3', H5) on IDO 5.3 -> results2.json."""
import json

import probe
from test import b_next, cond_index, li, uncond

H = "extern void g(int, int);\nextern void h(void);\nextern void u(int);\n"


def c(src):
    return probe.compile_(H + src, function="f")


def back_kinds(insns):
    out = []
    for k, x in enumerate(insns):
        op = x.partition(" ")[0]
        if x.startswith("b") and op not in ("b", "break") and int(x.split(",")[-1], 16) // 4 <= k:
            prev = insns[k - 1].partition(" ")[0]
            out.append("slt" if op in ("bnez", "beqz") and prev.startswith("slt") else "ne" if op in ("bne", "beq") else op)
    return out


def loop(var, bound, arg):
    return f"    for ({var} = 0; {var} < {bound}; {var}++) {{\n        u({arg});\n    }}\n"


def main():
    r = {"H1'": {}, "H3'": {}, "H5": {}}
    a = c("void f(int a, int b) {\n    int x;\n    if (a == b) x = 7; else x = 3;\n    g(x, a);\n}\n")
    bn, ci = b_next(a), cond_index(a)
    r["H1'"]["P1'a"] = {"ok": len(bn) == 1 and any(k < ci for k in li(a, 3)) and (bn[0][0] + 1) in li(a, 7), "insns": a}
    b = c("void f(int a, int b) {\n    int x;\n    x = 3;\n    if (a == b) x = 7;\n    g(x, a);\n}\n")
    r["H1'"]["P1'b"] = {"ok": not uncond(b), "insns": b}
    ie = c("int f(int a, int b) {\n    int x;\n    h();\n    if (a == b) x = 7; else x = 3;\n    return x + a;\n}\n")
    do = c("int f(int a, int b) {\n    int x;\n    h();\n    x = 3;\n    if (a == b) x = 7;\n    return x + a;\n}\n")
    paths = lambda s: len(uncond(s)) + s.count("jr ra")
    r["H1'"]["P1'c"] = {"ok": paths(ie) == paths(do) + 1, "paths": [paths(ie), paths(do)], "if_else": ie, "default": do}
    ret = c("int f(int *p, int n, int k) {\n    int i;\n    for (i = 0; i < n; i++) {\n        h();\n"
            "        if (p[i] == k) return -1;\n    }\n    return -1;\n}\n")
    brk = c("int f(int *p, int n, int k) {\n    int i;\n    for (i = 0; i < n; i++) {\n        h();\n"
            "        if (p[i] == k) break;\n    }\n    return -1;\n}\n")
    r["H3'"]["P3'a"] = {"ok": len(li(ret, -1)) == 2, "count": len(li(ret, -1)), "insns": ret}
    r["H3'"]["P3'b"] = {"ok": len(li(brk, -1)) == 1, "count": len(li(brk, -1)), "insns": brk}
    decl = "    int i;\n    int j;\n    int k;\n"
    cases = {
        "P5a": (decl + loop("i", 5, "i") + loop("i", 7, "i + 1") + loop("i", 9, "i + 2"), ["slt", "slt", "ne"]),
        "P5b": (decl + loop("i", 5, "i") + loop("j", 7, "j + 1") + loop("k", 9, "k + 2"), ["ne", "ne", "ne"]),
        "P5c": (decl + loop("i", 5, "i") + loop("j", 7, "j + 1") + loop("i", 9, "i + 2"), ["slt", "ne", "ne"]),
        "P5d": (decl + "    for (i = 0; i < 5; i++) {\n        for (j = 0; j < 7; j++) {\n            u(i + j);\n"
                "        }\n    }\n" + loop("j", 9, "j + 2"), None),
    }
    for pid, (body, want) in cases.items():
        s = c("void f(void) {\n" + body + "}\n")
        kinds = back_kinds(s)
        if pid == "P5d":
            # order of backward branches in the object: inner, outer, last (inner closes first)
            want = ["slt", "ne", "ne"]
        r["H5"][pid] = {"ok": kinds == want, "kinds": kinds, "want": want, "insns": s}
    for h, preds in r.items():
        print(h, "confirmed" if all(p["ok"] for p in preds.values()) else "refuted",
              {k: (p["ok"], p.get("kinds") or p.get("count") or p.get("paths")) for k, p in preds.items()})
    (probe.Path(__file__).resolve().parent / "results2.json").write_text(json.dumps(r, indent=1, default=bool))


if __name__ == "__main__":
    main()

"""Run the PROTOCOL.md predictions H1-H4 on IDO 5.3 and write results.json."""
import json
import re

import probe

UNCOND = ("b", "j")


def ops(insns):
    return [i.partition(" ")[0] for i in insns]


def uncond(insns):
    """(index, target index) of every unconditional branch."""
    out = []
    for k, ins in enumerate(insns):
        op, _, rest = ins.partition(" ")
        if op in UNCOND:
            out.append((k, int(rest.split(",")[-1], 16) // 4))
    return out


def b_next(insns):
    return [(k, t) for k, t in uncond(insns) if t == k + 2]


def cond_index(insns):
    return next(k for k, ins in enumerate(insns)
                if ins.partition(" ")[0] not in UNCOND + ("jr", "jal", "jalr", "break") and ins.startswith("b"))


def li(insns, value):
    return [k for k, ins in enumerate(insns) if re.match(rf"li \w+,{value}$", ins)]


def epilogue_branches(insns):
    jr = [k for k, ins in enumerate(insns) if ins == "jr ra"]
    return [(k, t) for k, t in uncond(insns) if jr and t <= jr[-1] <= t + 3]


def c(src, opt=None):
    return probe.compile_(src, opt=opt, function="f")


def h1():
    r = {}
    G = "extern int g;\n"
    p1a = c("int f(int a, int b) {\n    int x;\n    if (a == b) x = 7; else x = 3;\n    return x + a;\n}\n")
    bn = b_next(p1a)
    ci = cond_index(p1a)
    r["P1a"] = {"ok": len(bn) == 1 and any(k < ci for k in li(p1a, 3)) and bn and (bn[0][0] + 1) in li(p1a, 7),
                "insns": p1a}
    p1b = c("int f(int a, int b) {\n    int x;\n    x = 3;\n    if (a == b) x = 7;\n    return x + a;\n}\n")
    r["P1b"] = {"ok": not uncond(p1b), "insns": p1b}
    p1c = c("int f(int a, int b) {\n    int x;\n    x = (a == b) ? 7 : 3;\n    return x + a;\n}\n")
    r["P1c"] = {"ok": p1c == p1a, "insns": p1c}
    p1d_ie = c("int f(int a, int b) {\n    int x;\n    if (a == b) x = a * 3; else x = b + 100;\n    return x + a;\n}\n")
    p1d_do = c("int f(int a, int b) {\n    int x;\n    x = b + 100;\n    if (a == b) x = a * 3;\n    return x + a;\n}\n")
    join_ie = [t for k, t in uncond(p1d_ie)]
    r["P1d"] = {"ok": bool(join_ie) and not uncond(p1d_do), "if_else": p1d_ie, "default": p1d_do}
    p1e_ie = c(G + "void f(int a, int b) {\n    if (a == b) g = 7; else g = 3;\n}\n")
    p1e_do = c(G + "void f(int a, int b) {\n    g = 3;\n    if (a == b) g = 7;\n}\n")
    r["P1e"] = {"ok": bool(uncond(p1e_ie)) and not uncond(p1e_do), "if_else": p1e_ie, "default": p1e_do}
    p1f = c("int f(int a, int b) {\n    int x;\n    x = 3;\n    if (a == b) { x = 7; } else { }\n    return x + a;\n}\n")
    r["P1f"] = {"ok": p1f == p1b, "insns": p1f}
    p1g = c("int f(int a, int b) {\n    int x;\n    if (a == b) x = 7; else if (a < b) x = 5; else x = 3;\n"
            "    return x + a;\n}\n")
    r["P1g"] = {"ok": len(uncond(p1g)) == 2, "insns": p1g}
    return r


def h2():
    r = {}
    p2a = c("int f(int a) {\n    int t = a * 3;\n    if (t > 10) return 1;\n    return 0;\n}\n", "-O1")
    r["P2a"] = {"ok": "addiu sp,sp,-8" in p2a and p2a.count("jr ra") == 1 and bool(epilogue_branches(p2a)),
                "insns": p2a}
    p2b = c("int f(int a) {\n    if (a * 3 > 10) return 1;\n    return 0;\n}\n", "-O1")
    r["P2b"] = {"ok": not any(i.startswith("addiu sp") for i in p2b) and p2b.count("jr ra") == 2, "insns": p2b}
    p2c = c("int f(int a) {\n    register int t = a * 3;\n    if (t > 10) return 1;\n    return 0;\n}\n", "-O1")
    r["P2c"] = {"ok": "addiu sp,sp,-8" in p2c and not any(re.match(r"sw \w+,\d+\(sp\)", i) for i in p2c),
                "insns": p2c}
    p2d = c("int f(int a) {\n    int t = a * 3;\n    int u = a + 5;\n    int v = t - u;\n"
            "    if (v > 10) return t;\n    return u;\n}\n", "-O1")
    r["P2d"] = {"ok": "addiu sp,sp,-16" in p2d, "insns": p2d}
    return r


def h3():
    r = {}
    p3a = c("int f(int *p, int n, int k) {\n    int i;\n    for (i = 0; i < n; i++) {\n"
            "        if (p[i] == k) return -1;\n    }\n    return -1;\n}\n")
    r["P3a"] = {"ok": len(li(p3a, -1)) == 2, "count": len(li(p3a, -1)), "insns": p3a}
    p3b = c("int f(int *p, int n, int k) {\n    int i;\n    for (i = 0; i < n; i++) {\n"
            "        if (p[i] == k) break;\n    }\n    return -1;\n}\n")
    r["P3b"] = {"ok": len(li(p3b, -1)) == 1, "count": len(li(p3b, -1)), "insns": p3b}
    return r


def h4():
    r = {}
    H = "extern void g(int);\n"
    forms = {
        "for": "void f(int n) {\n    int i;\n    for (i = 0; i < n; i++) {\n        g(i);\n    }\n}\n",
        "while": "void f(int n) {\n    int i;\n    i = 0;\n    while (i < n) {\n        g(i);\n        i++;\n    }\n}\n",
        "guarded_do": "void f(int n) {\n    int i;\n    if (n > 0) {\n        i = 0;\n        do {\n            g(i);\n"
                      "            i++;\n        } while (i < n);\n    }\n}\n",
        "goto": "void f(int n) {\n    int i;\n    i = 0;\n    if (n <= 0) goto end;\nloop:\n    g(i);\n"
                "    if (++i < n) goto loop;\nend:\n    ;\n}\n",
    }
    got = {k: c(H + v) for k, v in forms.items()}
    r["P4a"] = {"ok": all(v == got["for"] for v in got.values()),
                "equal_to_for": {k: v == got["for"] for k, v in got.items()}, "insns": got}
    fc = c(H + "void f(void) {\n    int i;\n    for (i = 0; i < 3; i++) {\n        g(i);\n    }\n}\n")
    dw = c(H + "void f(void) {\n    int i;\n    i = 0;\n    do {\n        g(i);\n        i++;\n    } while (i < 3);\n}\n")
    r["P4b"] = {"ok": fc == dw, "for": fc, "do": dw}
    return r


def main():
    res = {"H1": h1(), "H2": h2(), "H3": h3(), "H4": h4()}
    for h, preds in res.items():
        verdict = "confirmed" if all(p["ok"] for p in preds.values()) else "refuted"
        print(h, verdict, {k: p["ok"] for k, p in preds.items()})
    (probe.Path(__file__).resolve().parent / "results.json").write_text(json.dumps(res, indent=1, default=bool))


if __name__ == "__main__":
    main()

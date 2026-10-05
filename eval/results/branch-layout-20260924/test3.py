"""Amendment A2 predictions (H6) on IDO 5.3 -> results3.json."""
import json
import re

import probe

H = "extern void g(short, int);\nextern short p[];\n"


def c(body):
    return probe.compile_(H + "void f(void) {\n    int i;\n    int t;\n    int o;\n" + body + "}\n", function="f")


def loop_body(insns):
    back = [k for k, x in enumerate(insns) if x.startswith("b") and x.split(" ")[0] not in ("b", "break")
            and int(x.split(",")[-1], 16) // 4 <= k]
    k = back[-1]
    return insns[int(insns[k].split(",")[-1], 16) // 4:k + 2], insns[k]


def main():
    r = {}
    mul = c("    for (i = 0; i < 2; i++) {\n        g(p[i], i * 0x40);\n    }\n")
    hand = c("    t = 0;\n    o = 0;\n    do {\n        g(p[t], o);\n        o += 0x40;\n        t++;\n    } while (o != 0x80);\n")
    shl = c("    for (i = 0; i < 2; i++) {\n        g(p[i], i << 6);\n    }\n")
    r["P6a"] = {"ok": mul != hand, "mul": mul, "hand": hand}
    body_mul, _ = loop_body(mul)
    body_shl, _ = loop_body(shl)
    r["P6b"] = {"ok": not any(x.startswith("sll") for x in body_mul) and any(x.startswith("sll") for x in body_shl),
                "shl": shl}
    _, test = loop_body(mul)
    bound = test.split(" ")[1].split(",")[1] if test.startswith("bne") else None
    r["P6c"] = {"ok": bool(bound) and any(re.match(rf"li {bound},128$", x) for x in mul), "test": test}
    verdict = "confirmed" if all(v["ok"] for v in r.values()) else "refuted"
    print("H6", verdict, {k: v["ok"] for k, v in r.items()}, r["P6c"]["test"])
    (probe.Path(__file__).resolve().parent / "results3.json").write_text(json.dumps(r, indent=1, default=bool))


if __name__ == "__main__":
    main()

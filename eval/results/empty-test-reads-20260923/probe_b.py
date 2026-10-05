"""H16b: constant and copy initialisers where x cannot be propagated away (PROTOCOL.md)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
BODY = ("void syn_f(int n) {\n    int x;\n    if (n) {\n        x = {E};\n{P}    } else {\n        x = syn_a[n];\n    }\n"
        "    syn_a[1] = x;\n    syn_g(n);\n    syn_a[2] = x;\n    syn_a[3] = x;\n}\n")
CASES = {"5": 0, "n": 0, "n + 1": 1}


def main():
    command = ai.recipe(TREE)
    rows, verdicts = {}, []
    for e, predicted in CASES.items():
        src = ai.HEADER + BODY.replace("{E}", e)
        base = ai.ranges_by_offset(TREE, command, src.replace("{P}", ""))
        probe = ai.ranges_by_offset(TREE, command, src.replace("{P}", "        if (!x);\n"))
        row = {"base": base.get(-4), "probe": probe.get(-4), "predicted": predicted}
        if row["base"] is None or row["probe"] is None:
            row["verdict"] = "untestable"
        else:
            row["delta_save"] = round(row["probe"][0] - row["base"][0], 3)
            row["delta_span"] = row["probe"][1] - row["base"][1]
            row["verdict"] = row["delta_save"] == predicted
        rows[e] = row
        verdicts.append(row["verdict"])
        print(e, json.dumps(row), flush=True)
    testable = [v for v in verdicts if isinstance(v, bool)]
    verdict = ("confirmed" if testable and all(testable) and len(testable) == len(verdicts)
               else "untestable" if not testable else "not confirmed") + f" ({sum(testable)}/{len(testable)} testable)"
    print(verdict)
    (HERE / "results_b.json").write_text(json.dumps({"verdict": verdict, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()

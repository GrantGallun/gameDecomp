"""Preference sources (PROTOCOL.md): traced synthetic compiles, read the local's block-row preferred colours."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402
from solver import uopt_trace  # noqa: E402

HERE = Path(__file__).resolve().parent
TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
HDR = ("extern int syn_a[];\nextern int syn_g(int);\nextern void syn_h1(int);\nextern void syn_h2(int, int);\n"
       "extern void syn_h3(int, int, int);\nextern void syn_h4(int, int, int, int);\n")
X = "    int x;\n    x = syn_a[n] + n;\n"
CASES = {
    "arg0": ("void syn_f(int n) {\n" + X + "    syn_h1(x);\n    syn_a[1] = x;\n}\n", {3}),
    "arg1": ("void syn_f(int n) {\n" + X + "    syn_h2(0, x);\n    syn_a[1] = x;\n}\n", {4}),
    "arg2": ("void syn_f(int n) {\n" + X + "    syn_h3(0, 0, x);\n    syn_a[1] = x;\n}\n", {5}),
    "arg3": ("void syn_f(int n) {\n" + X + "    syn_h4(0, 0, 0, x);\n    syn_a[1] = x;\n}\n", {6}),
    "return": ("int syn_f(int n) {\n" + X + "    syn_a[1] = x;\n    return x;\n}\n", {1}),
    "call_result": ("void syn_f(int n) {\n    int x;\n    x = syn_g(n);\n    syn_a[1] = x;\n    syn_a[2] = x + n;\n}\n", {1}),
    "none": ("void syn_f(int n) {\n" + X + "    syn_a[1] = x;\n    syn_a[2] = x * 3;\n}\n", set()),
}


def main():
    cmd = ai.recipe(TREE)
    rows = []
    for name, (body, predicted) in CASES.items():
        _o, l5 = ai.compile_traced(TREE, cmd, HDR + body, 5)
        _o, l6 = ai.compile_traced(TREE, cmd, HDR + body, 6)
        proc = uopt_trace.join(l5, l6)["syn_f"]
        xs = [r for r in proc.ranges.values() if r.kind == "M"]
        prefs = sorted({p for r in xs for p in r.preferences()})
        colours = sorted({r.color for r in xs})
        params = [(r.preferences(), r.color) for r in proc.ranges.values() if r.kind == "P"]
        held = (set(prefs) == set()) if not predicted else predicted <= set(prefs)
        rows.append({"case": name, "x_ranges": len(xs), "x_preferences": prefs, "x_colours": colours,
                     "predicted": sorted(predicted), "held": held if xs else None, "params": params})
        print(json.dumps(rows[-1]))
    verdicts = {r["case"]: ("untestable" if r["held"] is None else "confirmed" if r["held"] else "refuted") for r in rows}
    (HERE / "results.json").write_text(json.dumps({"rows": rows, "verdicts": verdicts}, indent=1))
    print(json.dumps(verdicts, indent=1))


if __name__ == "__main__":
    main()

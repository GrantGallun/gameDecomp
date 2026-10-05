"""H5 interventions (protocol: eval/results/allocator-rules-20260923): paired compiles through IDO 5.3's traced uopt.

Each case is a synthetic `syn_*` function with exactly one local (`x`, isvar kind M), so its live range is found
without guessing. A variant changes one thing. Measured per compile: x's adjsave, block span, and
save = adjsave * units(span) (H1', confirmed post-hoc on 11,808 of 11,809 census ranges); and the object bytes.
Compiles use a game TU's own recipe from the dedicated trace tree, with only the source and output replaced.

    python -m eval.allocator_interventions --tree ~/decomp/tools-src/sbk1-trace-gate --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from eval.allocator_rules import span, units
from eval.uopt_trace_census import compile_commands
from solver import byte_certificate, uopt_trace

HEADER = "extern int syn_a[];\nextern int syn_g(int);\n"
# x must actually get a register: in a two-call function its range had adjsave 0 and stayed on the stack.
BASES = {
    "leaf": "void syn_f(int n) {\n    int x;\n    x = syn_a[n];\n    syn_a[1] = x;\n    syn_a[2] = x;\n}\n",
    "leaf_branch": "void syn_f(int n) {\n    int x;\n    x = syn_a[n];\n    if (n) {\n        syn_a[1] = x;\n    }\n"
                   "    syn_a[2] = x;\n}\n",
    "across_call": "void syn_f(int n) {\n    int x;\n    x = syn_a[n];\n    syn_a[1] = x;\n    syn_g(n);\n"
                   "    syn_a[2] = x;\n    syn_a[3] = x;\n    syn_a[4] = x;\n}\n",
}
# name -> (edit applied before the closing brace, what 7.1 predicts)
VARIANTS = {
    "extra_read": ("    syn_a[5] = x;\n", "save +10 (one reference)"),
    "if_not": ("    if (!x);\n", "save +10, same as one reference"),
    "bare_expression": ("    x;\n", "no effect: object and trace identical"),
    "loop_read": ("    while (n--) {\n        syn_a[n] = x;\n    }\n", "save +10, no loop weighting"),
    "extra_write": ("    x = syn_a[x];\n", "save +10 (one reference)"),
}


def recipe(tree: Path) -> str:
    """A game TU's own -O2 compile (e.g. src/race). COMPILING_LIBULTRA is defined for game code too."""
    for obj, command, _post in compile_commands(tree):
        if " -O2 " in command and "/race/" in obj:
            return command
    raise RuntimeError("no game-recipe compile command found")


def cc_flags(command: str) -> list[str]:
    """The IDO cc flags: after asm-processor's second `--`, without the output and source.

    The synthetic file has no GLOBAL_ASM, so asm-processor has nothing to do and cc is called directly.
    """
    args = shlex.split(command)
    flags = args[[i for i, a in enumerate(args) if a == "--"][1] + 1:]
    out, skip = [], False
    for a in flags:
        if skip:
            skip = False
        elif a == "-o":
            skip = True
        elif not a.endswith(".c"):
            out.append(a)
    return out


def compile_traced(tree: Path, command: str, source: str, level: int) -> tuple[bytes, str]:
    # One fixed path: the source path is recorded in the object's .mdebug and in the trace.
    probe = tree / "build" / "syn_probe"
    probe.mkdir(parents=True, exist_ok=True)
    c, o = probe / "syn.c", probe / "syn.o"
    if True:
        c.write_text(source)
        args = [str(Path.home() / "decomp/tools-src/ido-trace/cc"), *cc_flags(command),
                f"-Wo,-zdbug:{level}", "-o", str(o), str(c)]
        (tree / "uoptlist").unlink(missing_ok=True)
        run = subprocess.run(args, cwd=tree, capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(run.stderr[-600:])
        return o.read_bytes(), (tree / "uoptlist").read_text(errors="replace")


def measure(tree, command, source) -> dict:
    obj, l5 = compile_traced(tree, command, source, 5)
    _obj6, l6 = compile_traced(tree, command, source, 6)
    proc = uopt_trace.join(l5, l6)["syn_f"]
    nodes = {r.node for r in proc.ranges.values() if r.kind == "M"}
    image = byte_certificate.object_image(obj)          # sections and relocations; not .mdebug paths or dates
    trace = "\n".join(l for l in (l5 + l6).splitlines() if "SECONDS" not in l)
    out = {"object_sha256": hashlib.sha256(json.dumps(image, sort_keys=True, default=str).encode()).hexdigest(),
           "trace_sha256": hashlib.sha256(trace.encode()).hexdigest(), "local_nodes": len(nodes)}
    if len(nodes) == 1:
        pieces = [r for r in proc.ranges.values() if r.node in nodes and r.adjsave is not None]
        out["pieces"] = [{"adjsave": r.adjsave, "span": span(r), "colour": r.color} for r in pieces]
        coloured = [r for r in pieces if r.color > 0]
        if len(pieces) == 1 and coloured:
            r = pieces[0]
            out.update(adjsave=r.adjsave, span=span(r), save=round(r.adjsave * units(span(r)), 3), colour=r.color)
    return out


H7_SOURCE = HEADER + ("void syn_f(int n) {\n    int {d0};\n    int {d1};\n    int {d2};\n"
                      "    a = syn_a[n];\n    b = syn_a[n + 1];\n    c = syn_a[n + 2];\n"
                      "    syn_a[3] = a;\n    syn_a[4] = b;\n    syn_a[5] = c;\n    syn_a[6] = a + b + c;\n}\n")
H6_BASE = HEADER + ("void syn_f(int n) {\n    int x;\n    x = 0;\n    if (n > 0) {\n        do {\n"
                    "            syn_a[x] = n;\n            x += 1;\n        } while (x < n);\n    }\n    syn_g(n);\n}\n")


def ranges_by_offset(tree, command, source):
    _obj, l5 = compile_traced(tree, command, source, 5)
    _obj6, l6 = compile_traced(tree, command, source, 6)
    proc = uopt_trace.join(l5, l6)["syn_f"]
    return {r.offset: (round(r.adjsave * units(span(r)), 3), span(r)) for r in proc.ranges.values()
            if r.kind == "M" and r.adjsave is not None}


def h7(tree, command) -> dict:
    """Frame offset of each local vs declaration order, mapped by the confirmed +1-read probe (values are
    loads, so the probe cannot be constant-folded)."""
    rows = []
    for order in (("a", "b", "c"), ("c", "a", "b"), ("b", "c", "a")):
        src = H7_SOURCE.replace("{d0}", order[0]).replace("{d1}", order[1]).replace("{d2}", order[2])
        base = ranges_by_offset(tree, command, src)
        offset_of = {}
        for v in "abc":
            probe = src.replace(f"    {v} = syn_a", f"    {v} = syn_a", 1)
            at = probe.index(f"    {v} = syn_a")
            at = probe.index("\n", at) + 1
            probe = probe[:at] + f"    if (!{v});\n" + probe[at:]
            after = ranges_by_offset(tree, command, probe)
            # save (adjsave x units(span)) rises by exactly one read; the unfolded test may add blocks too
            rose = [o for o in base if o in after and after[o][0] - base[o][0] == 1]
            offset_of[v] = rose[0] if len(rose) == 1 else None
        rows.append({"declared": list(order), "offset_of": offset_of,
                     "offsets_in_declaration_order": [offset_of[v] for v in order]})
    mapped = [r for r in rows if None not in r["offset_of"].values()]
    rule = all(r["offsets_in_declaration_order"] == [-4, -8, -12] for r in mapped)
    return {"rows": rows, "mapped_orders": len(mapped),
            "verdict": "untestable" if not mapped else "confirmed" if rule and len(mapped) == len(rows) else "refuted"}


def h6(tree, command) -> dict:
    """`if (!x);` right after `x = 0;`: folded (no +1 read), yet does it add blocks to ranges live across it?"""
    base = ranges_by_offset(tree, command, H6_BASE)
    probe = H6_BASE.replace("    x = 0;\n", "    x = 0;\n    if (!x);\n", 1)
    after = ranges_by_offset(tree, command, probe)
    rows = {o: {"base": base.get(o), "after": after.get(o)} for o in set(base) | set(after)}
    folded = all(v["base"] and v["after"] and v["after"][0] == v["base"][0] for v in rows.values())
    spans_grew = any(v["base"] and v["after"] and v["after"][1] > v["base"][1] for v in rows.values())
    return {"rows": {str(k): v for k, v in rows.items()}, "save_unchanged": folded, "span_grew": spans_grew}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    command = recipe(args.tree)
    rows = []
    for base_name, body in BASES.items():
        base = measure(args.tree, command, HEADER + body)
        for name, (edit, prediction) in VARIANTS.items():
            variant_src = HEADER + body[: body.rindex("}")] + edit + "}\n"
            v = measure(args.tree, command, variant_src)
            row = {"base": base_name, "variant": name, "prediction_7_1": prediction,
                   "save": [base.get("save"), v.get("save")], "span": [base.get("span"), v.get("span")],
                   "delta_save": None if base.get("save") is None or v.get("save") is None
                                 else round(v["save"] - base["save"], 3),
                   "object_identical": base["object_sha256"] == v["object_sha256"],
                   "trace_identical": base["trace_sha256"] == v["trace_sha256"]}
            rows.append(row)
            print(json.dumps(row))
    # H5a: `if (!x);` changes save exactly as one extra read. H5b: a bare `x;` changes nothing.
    by = {(r["base"], r["variant"]): r for r in rows}
    # A comparison with a missing measurement is untestable, never agreement (None == None is not evidence).
    h5a = [(by[(b, "if_not")]["delta_save"], by[(b, "extra_read")]["delta_save"]) for b in BASES]
    h5a = [a == r for a, r in h5a if a is not None and r is not None]
    h5b = [by[(b, "bare_expression")]["object_identical"] and by[(b, "bare_expression")]["trace_identical"] for b in BASES]
    verdict = {"H5a_if_not_equals_one_reference": "untestable" if not h5a else "confirmed" if all(h5a) else "refuted",
               "H5a_measured_bases": len(h5a),
               "H5b_bare_expression_inert": "confirmed" if all(h5b) else "refuted",
               "save_per_read": sorted({by[(b, 'extra_read')]['delta_save'] for b in BASES}),
               "save_per_loop_read": sorted({by[(b, 'loop_read')]['delta_save'] for b in BASES}),
               "save_per_write": sorted({by[(b, 'extra_write')]['delta_save'] for b in BASES})}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "interventions.json").write_text(json.dumps({"recipe": command, "rows": rows, "verdict": verdict}, indent=1))
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

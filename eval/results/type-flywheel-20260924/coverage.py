"""Coverage of the SOLVED harvest over functions' own assembly (binary evidence only). No compiles.

For each function: globals it references (%hi/%lo and %gp relocation symbols in target.s) and callees (jal targets).
Covered = the harvest holds a verified declaration: a global's extern, a callee's own matched signature, or a callee
prototype from a matched source. Also whether the function's OWN prototype appears in a matched caller.
Populations: the 201 unsolved restart-round-3 functions, and every never-exact workspace function.

    python3 coverage.py -> coverage.json"""
import collections
import json
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = Path.home() / "decomp/experiments/type-flywheel-20260924"
REPO = Path.home() / "decomp/sbk1"
POP = Path.home() / "decomp/experiments/restart-round3-20260923/rows"
RELOC = re.compile(r"%(?:hi|lo|gp_rel)\(([A-Za-z_]\w*)")
JAL = re.compile(r"\bjal\s+([A-Za-z_]\w*)")


def refs(name):
    s = (REPO / "nonmatchings" / name / "target.s")
    if not s.exists():
        return None
    text = s.read_text(errors="replace")
    glob = {g for g in RELOC.findall(text) if not g.startswith((".", "jtbl_", "L8", "D_8" + "0000000"))}
    calls = set(JAL.findall(text)) - {name}
    return glob - calls, calls


def main():
    h = json.loads((E / "harvest.json").read_text())
    sym = h["symbols"]
    solved = set(h["solved_sources"])
    conn = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    exact_any = {n for (n,) in conn.execute("select distinct f.name from attempts a join functions f on "
                                            "f.addr=a.func_addr where a.exact=1")}
    unsolved_pop = {json.loads(p.read_text())["function"] for p in POP.glob("*.json")
                    if not json.loads(p.read_text()).get("exact")}
    everything = {p.name for p in (REPO / "nonmatchings").iterdir() if p.is_dir()} - exact_any
    out = {}
    for label, names in (("unsolved_population", unsolved_pop), ("all_never_exact", everything)):
        g_tot = g_cov = c_tot = c_cov = own = 0
        fn_any = fn_half = 0
        per = []
        for n in sorted(names):
            r = refs(n)
            if r is None:
                continue
            glob, calls = r
            gc = {g for g in glob if "global" in sym.get(g, {})}
            cc = {c for c in calls if c in solved or "prototype" in sym.get(c, {}) or "signature" in sym.get(c, {})}
            has_own = "prototype" in sym.get(n, {})
            g_tot += len(glob); g_cov += len(gc); c_tot += len(calls); c_cov += len(cc); own += has_own
            total, cov = len(glob) + len(calls), len(gc) + len(cc)
            fn_any += cov > 0
            fn_half += total > 0 and cov / total >= 0.5
            per.append({"function": n, "globals": len(glob), "globals_covered": len(gc), "calls": len(calls),
                        "calls_covered": len(cc), "own_prototype": has_own})
        out[label] = {"functions": len(per), "global_refs": g_tot, "global_refs_covered": g_cov,
                      "call_refs": c_tot, "call_refs_covered": c_cov, "own_prototype_known": own,
                      "functions_with_any_coverage": fn_any, "functions_half_covered": fn_half, "rows": per}
        print(label, {k: v for k, v in out[label].items() if k != "rows"})
    (HERE / "coverage.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

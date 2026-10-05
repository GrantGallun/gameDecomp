"""Exploration (not a test): among procedures with vreg locals and no memory locals (A1 instrument), what separates
area == 0 from area > 0? Features per procedure from the joined uopt trace; results feed a pre-registered H17b."""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval.allocator_rules import procedures  # noqa: E402
import vreg_frames as vf  # noqa: E402

HERE = Path(__file__).resolve().parent
CALLEE = set(range(14, 23))          # colours 14-21 s0..s7, 22 s8


def main():
    rows = {r["function"]: r for r in json.loads((HERE / "vreg_frames_a1.json").read_text())["rows"]}
    isv = {}
    for l5 in sorted(vf.TRACES.glob("*.l5")):
        isv.update(vf.isvars_by_proc(l5.read_text(errors="replace")))
    feats = []
    for proc in procedures(vf.TRACES):
        r = rows.get(proc.name)
        if not r or (r["half"] != "fit" and "--a2" not in sys.argv):
            continue
        vars_ = isv.get(proc.name, {})
        vreg_nodes = {n: o for n, (k, o, v) in vars_.items() if k == "M" and v}
        pieces = collections.defaultdict(list)
        for rr in proc.ranges.values():
            if rr.node in vreg_nodes:
                pieces[rr.node].append(rr.color)
        uncoloured = {n for n, cs in pieces.items() if any(c <= 0 for c in cs)}
        absent = {n for n in vreg_nodes if n not in pieces}           # listed but no live range at all
        caller = {n for n, cs in pieces.items() if cs and all(0 < c < 14 for c in cs)}
        depth_unc = max([-vreg_nodes[n] for n in uncoloured | absent] + [0])
        d5 = max([-vreg_nodes[n] for n in uncoloured] + [0])
        d6 = max([-vreg_nodes[n] for n in uncoloured | caller] + [0])
        feats.append({"function": proc.name, "half": r["half"], "d5": d5, "d6": d6,
                      "area": r["area"], "mem_depth": r["mem_depth"],
                      "vreg_depth": r["vreg_depth"], "n_vreg": len(vreg_nodes), "uncoloured": len(uncoloured),
                      "absent": len(absent), "caller_saved_only": len(caller), "depth_uncoloured": depth_unc})
    groups = collections.defaultdict(list)
    for f in feats:
        if f["mem_depth"] == 0 and f["vreg_depth"] > 0:
            groups["area>0" if f["area"] > 0 else "area=0"].append(f)
    summary = {}
    for g, fs in groups.items():
        summary[g] = {"n": len(fs),
                      "any_uncoloured": sum(f["uncoloured"] > 0 for f in fs),
                      "any_absent": sum(f["absent"] > 0 for f in fs),
                      "any_uncoloured_or_absent": sum(f["uncoloured"] + f["absent"] > 0 for f in fs),
                      "area_eq_align8_depth_uncoloured_or_absent": sum(vf.align8(f["depth_uncoloured"]) == f["area"] for f in fs)}
    if "--a2" in sys.argv:
        formulas = {"F0": lambda f: vf.align8(f["mem_depth"]),
                    "F5": lambda f: vf.align8(max(f["mem_depth"], f["d5"])),
                    "F6": lambda f: vf.align8(max(f["mem_depth"], f["d6"]))}
        for half in ("fit", "check"):
            part = [f for f in feats if f["half"] == half]
            summary[half] = {"n": len(part), **{k: round(sum(fn(f) == f["area"] for f in part) / max(1, len(part)), 4)
                                                for k, fn in formulas.items()}}
        best = max(("F5", "F6"), key=lambda k: summary["fit"][k])
        summary["best_on_fit"] = best
        summary["verdict"] = "confirmed" if summary["check"][best] >= 0.90 else "not confirmed"
        misses = [f for f in feats if f["half"] == "check" and formulas[best](f) != f["area"]]
        summary["check_miss_classes"] = dict(collections.Counter(
            f"{f['area'] - formulas[best](f):+d} mem={f['mem_depth'] > 0} unc={f['uncoloured']} caller={f['caller_saved_only']}"
            for f in misses).most_common(12))
        (HERE / "vreg_features_a2.json").write_text(json.dumps({"summary": summary, "features": feats}, indent=1))
        print(json.dumps(summary, indent=1))
        return
    print(json.dumps(summary, indent=1))
    (HERE / "vreg_features.json").write_text(json.dumps({"summary": summary, "features": feats}, indent=1))


if __name__ == "__main__":
    main()

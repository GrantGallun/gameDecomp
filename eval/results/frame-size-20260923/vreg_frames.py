"""H17: which uopt locals own frame bytes, fitted on half the census and checked on the other (PROTOCOL-vreg.md)."""
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval.frame_size import FRAME, SAVE_FLT, SAVE_INT, align8  # noqa: E402

HERE = Path(__file__).resolve().parent
TRACES = Path.home() / "decomp/tools-src/uopt-trace-census/traces"
REPO = Path.home() / "decomp/sbk1"
PROC = re.compile(r"^PROCEDURE\s+(\w+)|^\s*procedure\s+(\w+)", re.M)
ISVAR = re.compile(r"^\{\s*(\d+)\|\s*\d+\}\s+\d+\s+isvar\s+([A-Z])\s+\d+\s+(-?\d+)(vreg)?", re.M)


def isvars_by_proc(l5_text):
    """procedure name -> {node: (kind, offset, vreg)} from the level-5 listing."""
    from solver import uopt_trace
    out, upcoming = {}, {}
    # as in uopt_trace.parse_level5: isvar lines are listed before the timing line that names their procedure
    for line in l5_text.splitlines():
        m = ISVAR.match(line)
        if m:
            upcoming[int(m.group(1))] = (m.group(2), int(m.group(3)), bool(m.group(4)))
            continue
        timing = uopt_trace._TIMING.search(line)
        if timing:
            out.setdefault(timing.group(1), {}).update(upcoming)
            upcoming = {}
    return out


def observed(dump):
    lines = [l.strip() for l in dump.splitlines() if l.strip()]
    text = "\n".join(lines)
    m = FRAME.search("\n".join(lines[:3]))
    if not m:
        return None
    frame = int(m.group(1), 0)
    prologue = "\n".join(lines[:24])
    si, sf = len(set(SAVE_INT.findall(prologue))), len(set(SAVE_FLT.findall(prologue)))
    calls = bool(re.search(r"^(jal|jalr)\b", text, re.M))
    floor = frame - 4 * si - 8 * sf
    slots = [int(m.group(1), 0) for m in re.finditer(r"^s[bhw]c?1?\s+\$?\w+,(0x[0-9a-f]+|\d+)\(sp\)", text, re.M)]
    outgoing = max([16] + [s + 4 for s in slots if 16 <= s < floor]) if calls else 0
    out = {"frame": frame, "saved_int": si, "saved_float": sf, "outgoing": outgoing,
           "area": frame - align8(outgoing) - align8(4 * si + 8 * sf)}
    if AMENDED:
        # A1: everything above the highest save slot is the locals area; no outgoing or save-size model
        ends = [int(m.group(2), 0) + 4 for m in re.finditer(
            r"^sw\s+(s[0-8]|ra|fp),(0x[0-9a-f]+|\d+)\(sp\)", prologue, re.M)]
        ends += [int(m.group(2), 0) + (8 if m.group(1) == "sdc1" else 4) for m in re.finditer(
            r"^(sdc1|swc1)\s+\$?f(?:2[02468]|30),(0x[0-9a-f]+|\d+)\(sp\)", prologue, re.M)]
        if not ends:
            return None
        out["area"] = frame - align8(max(ends))
    return out


AMENDED = "--a1" in sys.argv


FORMULAS = {
    "F0": lambda m, v: align8(m),
    "F1": lambda m, v: align8(max(m, v)),
    "F2": lambda m, v: align8(m) + align8(v) if m > 0 else 0,
    "F3": lambda m, v: align8(m) + align8(v),
}


def main():
    rows = []
    for l5 in sorted(TRACES.glob("*.l5")):
        for name, vars_ in isvars_by_proc(l5.read_text(errors="replace")).items():
            dump = REPO / "nonmatchings" / name / "target_object_dump_normalized.s"
            if not dump.exists():
                continue
            obs = observed(dump.read_text(errors="replace"))
            if not obs:
                continue
            mem = max([-o for k, o, v in vars_.values() if k == "M" and not v] + [0])
            vreg = max([-o for k, o, v in vars_.values() if k == "M" and v] + [0])
            half = "fit" if int(hashlib.sha256(name.encode()).hexdigest(), 16) % 2 == 0 else "check"
            rows.append({"function": name, "half": half, "mem_depth": mem, "vreg_depth": vreg,
                         "n_vreg": sum(1 for k, o, v in vars_.values() if k == "M" and v), **obs})
    usable = [r for r in rows if r["area"] >= 0]
    result = {"procedures": len(rows), "instrument_failures": len(rows) - len(usable)}
    for half in ("fit", "check"):
        part = [r for r in usable if r["half"] == half]
        result[half] = {"n": len(part), **{f: round(sum(fn(r["mem_depth"], r["vreg_depth"]) == r["area"] for r in part)
                                                / max(1, len(part)), 4) for f, fn in FORMULAS.items()}}
    best = max(FORMULAS, key=lambda f: result["fit"][f])
    check = [r for r in usable if r["half"] == "check"]
    misses = [r for r in check if FORMULAS[best](r["mem_depth"], r["vreg_depth"]) != r["area"]]
    result["best_on_fit"] = best
    result["check_rate_of_best"] = result["check"][best]
    result["verdict"] = "confirmed" if result["check"][best] >= 0.90 else "not confirmed"
    result["miss_classes"] = dict(collections.Counter(
        (r["area"] - FORMULAS[best](r["mem_depth"], r["vreg_depth"]), r["mem_depth"] > 0, r["vreg_depth"] > 0)
        .__repr__() for r in misses).most_common(15))
    # the landscape: observed area by (mem_depth>0, vreg_depth>0)
    land = collections.defaultdict(collections.Counter)
    for r in usable:
        land[f"mem>0={r['mem_depth'] > 0} vreg>0={r['vreg_depth'] > 0}"][r["area"] - align8(r["mem_depth"])] += 1
    result["area_minus_F0_by_shape"] = {k: dict(v.most_common(8)) for k, v in land.items()}
    result["misses"] = misses[:40]
    (HERE / ("vreg_frames_a1.json" if AMENDED else "vreg_frames.json")).write_text(
        json.dumps({"result": result, "rows": rows}, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "misses"}, indent=1))


if __name__ == "__main__":
    main()

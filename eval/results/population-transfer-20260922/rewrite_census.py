"""Zero-compile: which solver/rewrites.py families fire on each expanded-arm ROOT (and best node).

Answers whether the diff-driven owners that variants() never calls would have anything to say on
the population's residuals. Optional argv: function names to emit probe sources for.
"""
import collections, inspect, json, sys
from pathlib import Path
OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from solver import rewrites  # noqa: E402
NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
ALREADY = {"byte_pointer_step_rewrites", "pointer_element_width_rewrites", "pointer_difference_scale_rewrites",
           "compare_swap_rewrites", "branch_sentinel_rewrites", "signed_compare_rewrites",
           "inline_temporary_rewrites", "statement_order_rewrites", "do_while_restore_rewrites"}
FAMILIES = [(n, f) for n, f in inspect.getmembers(rewrites, inspect.isfunction)
            if n.endswith("_rewrites") and list(inspect.signature(f).parameters)[:2] == ["code", "diff"]
            and n not in ALREADY]


def fire(source, diff):
    out = {}
    for n, f in FAMILIES:
        try:
            cands = [r(source) for r in f(source, diff)]
        except Exception as exc:                     # a raising owner is a finding, not a silent zero
            out[n] = f"raised {type(exc).__name__}"
            continue
        cands = [c for c in cands if c != source]
        if cands:
            out[n] = cands
    return out


def main():
    wanted = set(sys.argv[1:])
    counts, raised, per_fn, probes = collections.Counter(), collections.Counter(), {}, []
    for p in sorted((NATIVE / "rows").glob("*--expanded.json")):
        r = json.loads(p.read_text())
        if not r.get("world") or r.get("baseline_exact"):
            continue
        root = json.loads(Path(r["world"]).read_text())["world"]["nodes"][0]
        if not root["verdict"]["compiled"]:
            continue
        fired = fire(root["source"], root["verdict"].get("diff") or "")
        per_fn[r["function"]] = {n: (len(v) if isinstance(v, list) else v) for n, v in fired.items()}
        for n, v in fired.items():
            (counts if isinstance(v, list) else raised)[n] += 1
            if r["function"] in wanted and isinstance(v, list):
                for i, c in enumerate(v[:3]):
                    probes.append({"function": r["function"], "label": f"root:{n}:{i}", "source": c})
    print("families examined:", len(FAMILIES), sorted(n for n, _f in FAMILIES))
    print("roots:", len(per_fn), "roots with >=1 unexposed family firing:", sum(bool(v) for v in per_fn.values()))
    for n, c in counts.most_common():
        print(f"  {n:40} fires on {c:3} roots")
    if raised:
        print("RAISED:", dict(raised))
    (OUT / "rewrite-census.json").write_text(json.dumps({"families": sorted(n for n, _f in FAMILIES),
        "fires": dict(counts), "raised": dict(raised), "per_function": per_fn}, indent=1))
    if probes:
        (OUT / "probes-rewrites.json").write_text(json.dumps(probes, indent=1))
        print("probe sources written:", len(probes))


if __name__ == "__main__":
    main()

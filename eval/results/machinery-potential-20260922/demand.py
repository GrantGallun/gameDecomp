"""Mechanism potential: how often a mechanism's target residual occurs vs how often the mechanism fires on it.

Demand is detected from the compiler diff, independently of any mechanism's own guard. Coverage is whether a
mechanism documented for that residual proposes anything on that node (measured firing, nodes.jsonl). The
gap -- target residual present, owner silent -- is capability the mechanism was written for but does not
reach. Effectiveness when it does act comes from the machinery card. No compiles.
"""
import collections
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import signals  # noqa: E402

NODES = HERE.parent / "measured-potential-20260922/nodes.jsonl"
CARD = HERE.parent / "machinery-capability-20260922/report_card.json"
SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]

# The residual each mechanism documents as its target (its docstring's claim, which is what is under test).
OWNERS = {
    "offset": ["owner:layout", "owner:per_object_layout", "owner:shared_layout", "field_local"],
    "width": ["owner:global_load_signedness", "local_type", "typed_reread", "owner:narrow_increment_type"],
    "reloc": ["owner:reloc_symbol", "owner:reloc_padding"],
    "immediate": ["owner:immediate", "residual_evidence"],
    "ordering": ["stmt_order", "stmt_move"],
    "spill": ["register_storage", "owner:stack_home_padding", "single_use", "store_value_local"],
    "mask": ["owner:drop_mask", "single_use", "truth_test"],
}
SP = re.compile(r"^(?:lw|sw|lh|sh|lb|sb|lhu|lbu)\s+\w+,-?(?:0x)?[0-9a-f]+\(sp\)")
MASK = re.compile(r"^andi\s+\w+,\w+,(?:0xff|0xffff|255|65535)$")


def classes(diff):
    s = signals.analyse(diff, 50.0, False, True)
    minus = [l[1:].strip() for l in diff.splitlines() if l.startswith("-") and not l.startswith("---")]
    plus = [l[1:].strip() for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    out = {k for k in ("offset", "width", "reloc", "immediate", "ordering") if getattr(s, k)}
    if sum(bool(SP.match(l)) for l in plus) > sum(bool(SP.match(l)) for l in minus):
        out.add("spill")                                   # candidate-only stack traffic
    if sum(bool(MASK.match(l)) for l in plus) > sum(bool(MASK.match(l)) for l in minus):
        out.add("mask")                                    # a byte/half mask the target does not have
    return out


def main():
    fires = {}
    for line in NODES.open():
        r = json.loads(line)
        if "fires" in r:
            fires[(r["function"], r["arm"], r["id"])] = r["fires"]
    demand = collections.defaultdict(set)         # class -> functions with the residual somewhere
    covered = collections.defaultdict(set)        # class -> functions where an owner fired on such a node
    by_owner = collections.defaultdict(lambda: collections.Counter())
    examples = collections.defaultdict(list)
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world"):
                continue
            world = json.loads(Path(row["world"]).read_text())["world"]
            for n in world["nodes"]:
                v = n["verdict"]
                key = (row["function"], row["arm"], n["id"])
                if not v["compiled"] or v["exact"] or key not in fires:
                    continue
                present = classes(v.get("diff") or "")
                for c in present:
                    demand[c].add(row["function"])
                    fired = [o for o in OWNERS[c] if fires[key].get(o)]
                    for o in fired:
                        by_owner[c][o] += 1
                    if fired:
                        covered[c].add(row["function"])
                    elif len(examples[c]) < 400:
                        examples[c].append({"function": row["function"], "arm": row["arm"], "node": n["id"]})
    card = json.loads(CARD.read_text()) if CARD.exists() else {}
    report = {}
    for c in OWNERS:
        d, cov = demand[c], covered[c]
        report[c] = {"functions_with_residual": len(d), "functions_owner_fired": len(cov),
                     "untapped_functions": len(d - cov), "coverage": round(len(cov) / len(d), 3) if d else None,
                     "owner_fires_on_nodes": dict(by_owner[c]),
                     "owner_effect_when_acting": {o: {k: card[o][k] for k in ("of_acted_improved", "of_acted_clean", "exact")}
                                                  for o in OWNERS[c] if o in card},
                     "untapped_examples": sorted({e["function"] for e in examples[c]})[:12]}
    (HERE / "demand.json").write_text(json.dumps(report, indent=1))
    print(f"{'residual':10} {'functions':>9} {'owner fired':>11} {'untapped':>8} {'coverage':>8}  owners that fired")
    for c, r in report.items():
        print(f"{c:10} {r['functions_with_residual']:9} {r['functions_owner_fired']:11} {r['untapped_functions']:8} "
              f"{str(r['coverage']):>8}  {r['owner_fires_on_nodes']}")


if __name__ == "__main__":
    main()

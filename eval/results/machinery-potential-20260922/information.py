"""Information view of each mechanism: guessing it leaves vs evidence it converts. No compiles.

When a mechanism fires it offers k candidates; if one is right, a mechanism that fully used the evidence would
offer 1. So log2(mean k) is the search it leaves to the compiler, and p (improve rate per candidate) measures
how much of its guessing is informed. A blind mechanism has p near the base rate of all mutations; an
evidence-reading one has high p and small k. For evidence-determined residuals (offset, reloc, immediate,
mask: the diff states the target value) the information ceiling is ~0 bits of search, so their gap to that
ceiling is implementation.
"""
import collections
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODES = HERE.parent / "measured-potential-20260922/nodes.jsonl"
CARD = json.loads((HERE.parent / "machinery-capability-20260922/report_card.json").read_text())
DETERMINED = {"owner:layout", "owner:per_object_layout", "owner:reloc_symbol", "owner:immediate", "owner:drop_mask",
              "owner:global_load_signedness", "residual_evidence", "owner:frame_padding"}


def main():
    offered = collections.defaultdict(list)
    for line in NODES.open():
        r = json.loads(line)
        for fam, k in (r.get("fires") or {}).items():
            offered[fam].append(k)
    base_edges = sum(c["edges"] for c in CARD.values())
    base_p = sum(c["edges"] * c["acted"] * c["of_acted_improved"] + c["exact"] for c in CARD.values()) / base_edges
    rows = []
    for fam, ks in offered.items():
        if fam not in CARD or CARD[fam]["edges"] < 5:
            continue
        c = CARD[fam]
        k = sum(ks) / len(ks)
        p = (c["edges"] * c["acted"] * c["of_acted_improved"] + c["exact"]) / c["edges"]
        # Bits of evidence the mechanism converts per candidate, relative to blind mutation: log2(p / base_p).
        informed = math.log2(p / base_p) if p > 0 else float("-inf")
        rows.append((fam, round(k, 1), round(math.log2(k), 2) if k > 0 else 0, round(p, 3), round(informed, 2),
                     fam in DETERMINED, c["edges"]))
    rows.sort(key=lambda r: -r[4])
    out = [{"mechanism": f, "mean_candidates_when_firing": k, "search_bits_left": b, "improve_per_candidate": p,
            "informed_bits_vs_blind": i, "evidence_determined_class": d, "edges": e} for f, k, b, p, i, d, e in rows]
    (HERE / "information.json").write_text(json.dumps({"base_improve_per_candidate": round(base_p, 3), "rows": out}, indent=1))
    print(f"base improve per candidate (all mechanisms): {base_p:.3f}")
    print(f"{'mechanism':30} {'k':>6} {'bits left':>9} {'p(improve)':>10} {'bits vs blind':>13} determined")
    for f, k, b, p, i, d, e in rows:
        print(f"{f:30} {k:6} {b:9} {p:10} {i:13} {'yes' if d else ''}")


if __name__ == "__main__":
    main()

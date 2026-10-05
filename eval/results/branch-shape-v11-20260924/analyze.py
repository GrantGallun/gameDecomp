"""Paired analysis of the branch_shape v11 trial (PROTOCOL.md). No compiles.
Run 1: code-v10 vs the locality run on the 224 frozen sources (loss check). Run 2: round 4, control vs treatment."""
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import machinery_card  # noqa: E402

E = Path.home() / "decomp/experiments"
KINDS = ("select_else", "split_merge", "o1_register_local", "empty_then_return", "m2c_struct_copy", "dup_return_merge")


def solved(r):
    return bool(r and r.get("exact")) and not r.get("baseline_exact")


def rows(d):
    return {json.loads(p.read_text())["function"]: json.loads(p.read_text()) for p in (E / d / "rows").glob("*.json")}


def paired(old, new, label_old, label_new):
    pairs = [(f, old[f], new[f]) for f in sorted(new) if f in old]
    gains = [f for f, a, b in pairs if solved(b) and not solved(a)]
    losses = [f for f, a, b in pairs if solved(a) and not solved(b)]
    k, n = len(gains), len(gains) + len(losses)
    both_open = [(f, a, b) for f, a, b in pairs
                 if not solved(a) and not solved(b) and a.get("best_score") is not None and b.get("best_score") is not None]
    return {"functions": len(pairs), f"{label_old}_exact": sum(solved(a) for _f, a, _b in pairs),
            f"{label_new}_exact": sum(solved(b) for _f, _a, b in pairs), "gains": gains, "losses": losses,
            "sign_test_p_one_sided": sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else None,
            "gain_paths": {f: new[f].get("best_path_families") for f in gains},
            "open_improved": sum(b["best_score"] > a["best_score"] for _f, a, b in both_open),
            "open_worse": sum(b["best_score"] < a["best_score"] for _f, a, b in both_open),
            "open_mean_delta": round(sum(b["best_score"] - a["best_score"] for _f, a, b in both_open) / max(1, len(both_open)), 3),
            "worse": sorted(((f, a["best_score"], b["best_score"]) for f, a, b in both_open if b["best_score"] < a["best_score"]),
                            key=lambda x: x[2] - x[1])[:15],
            "improved": sorted(((f, a["best_score"], b["best_score"]) for f, a, b in both_open if b["best_score"] > a["best_score"]),
                               key=lambda x: x[1] - x[2])[:25],
            "worker_errors": [f for f, _a, b in pairs if b.get("status") != "ok"]}


def main():
    out = {}
    r1 = rows("branch-shape-v11-20260924")
    out["run1_vs_locality"] = paired(rows("locality-population-20260923"), r1, "locality", "v11")
    out["run1_vs_v10"] = paired(rows("branch-shape-population-20260924"), r1, "v10", "v11")
    card = machinery_card.card(machinery_card._worlds([E / "branch-shape-v11-20260924/rows"]))
    out["run1_card"] = {k: card[k] for k in KINDS if k in card}
    t11 = E / "branch-shape-round4-treatment-v11"
    if (t11 / "rows").exists():
        out["run2_v11_vs_control"] = paired(rows("branch-shape-round4-control"), rows(t11.name), "control", "v11")
        out["run2_v11_vs_v10"] = paired(rows("branch-shape-round4-treatment"), rows(t11.name), "v10", "v11")
        card = machinery_card.card(machinery_card._worlds([t11 / "rows"]))
        out["run2_card"] = {k: card[k] for k in KINDS if k in card}
    (HERE / "analysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1)[:6000])


if __name__ == "__main__":
    main()

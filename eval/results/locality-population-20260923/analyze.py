"""Paired comparison, promoted (code-v3) vs stage-2 routed, on the same 224 frozen sources. No compiles."""
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
FROZEN = json.loads((HERE / "freeze.json").read_text())
sys.path.insert(0, str(HERE.parents[2]))
from eval import machinery_card  # noqa: E402

NEW = Path.home() / "decomp/experiments/locality-population-20260923/rows"
OLD = Path.home() / "decomp/experiments/narrow-population-20260923/rows"


def solved(r):
    return bool(r and r.get("exact")) and not r.get("baseline_exact")


def main():
    new = {json.loads(p.read_text())["function"]: json.loads(p.read_text()) for p in NEW.glob("*.json")}
    old = {f: json.loads((OLD / f"{f}--narrow.json").read_text()) for f in new if (OLD / f"{f}--narrow.json").exists()}
    pairs = [(f, old[f], new[f]) for f in sorted(new) if f in old]
    gains = [f for f, a, b in pairs if solved(b) and not solved(a)]
    losses = [f for f, a, b in pairs if solved(a) and not solved(b)]
    k, n = len(gains), len(gains) + len(losses)
    both_open = [(f, a, b) for f, a, b in pairs if not solved(a) and not solved(b)]
    card = machinery_card.card(machinery_card._worlds([NEW]))
    result = {"functions": len(pairs), "narrow_exact": sum(solved(a) for _f, a, _b in pairs),
              "locality_exact": sum(solved(b) for _f, _a, b in pairs), "gains": gains, "losses": losses,
              "sign_test_p_one_sided": sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n if n else None,
              "gain_paths": {f: new[f].get("best_path_families") for f in gains},
              "open_improved": sum(b["best_score"] > a["best_score"] for _f, a, b in both_open),
              "open_worse": sum(b["best_score"] < a["best_score"] for _f, a, b in both_open),
              "open_mean_delta": round(sum(b["best_score"] - a["best_score"] for _f, a, b in both_open) / max(1, len(both_open)), 3),
              "compiles": {"narrow": sum(a.get("compiles", 0) for _f, a, _b in pairs),
                           "locality": sum(b.get("compiles", 0) for _f, _a, b in pairs)},
              "new_families": {k: card[k] for k in ("evidence_site", "frontend_type") if k in card},
              "receipt_errors": sum(bool(x.get("error")) for _f, _a, b in pairs for x in b.get("receipts", []))}
    (HERE / "analysis.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()


"""Score the pre-registered budget-96 run: did measured potential predict which functions closed?"""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def auc(pos, neg):
    """P(score of a random closed function > a random unclosed one); ties count half."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def main():
    prereg = json.loads((HERE / "preregistration96.json").read_text())
    potential_text = (HERE / "potential.json").read_bytes()
    assert hashlib.sha256(potential_text).hexdigest() == prereg["potential_sha256"], "potential changed after preregistration"
    potential = json.loads(potential_text)
    report = json.loads((HERE / "report96.json").read_text())
    confirmed = {c["function"] for c in report["confirmations"] if c["exact"]}
    rows = {r["function"]: r for r in report["rows"]}
    exact = {}
    for name, r in rows.items():
        if r.get("exact") and name in confirmed:
            world = json.loads(Path(r["world"]).read_text())["world"]
            order = [n["id"] for n in world["nodes"]]
            exact[name] = order.index(r["best_id"]) + 1                  # compile index of the exact node
    ranked = potential["ranked"]
    scores = {f: potential["scores"][f]["potential"] for f in ranked}
    quartiles = []
    for q in range(4):
        part = ranked[q * len(ranked) // 4:(q + 1) * len(ranked) // 4]
        quartiles.append({"quartile": q + 1, "functions": len(part), "exact": sum(f in exact for f in part),
                          "potential_range": [scores[part[-1]], scores[part[0]]]})
    result = {"functions": len(rows), "exact": len(exact), "exact_functions": exact,
              "beyond_32_compiles": sorted(f for f, i in exact.items() if i > 32),
              "within_32_compiles": sorted(f for f, i in exact.items() if i <= 32),
              "by_potential_quartile": quartiles,
              "auc_potential_for_exact": auc([scores[f] for f in exact], [scores[f] for f in ranked if f not in exact]),
              "compiles": sum(r.get("compiles", 0) for r in rows.values()),
              "infrastructure_errors": sum(bool(x.get("error")) for r in rows.values() for x in r.get("receipts", []))}
    (HERE / "analysis96.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

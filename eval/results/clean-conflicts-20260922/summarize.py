import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.quality_ratchet import compare

OUT = Path(__file__).resolve().parent
paired = json.loads((OUT / "paired.json").read_text())
assert len(paired["rows"]) == paired["expected"] == 200
arms = {}
for arm in ("before", "after"):
    rows = []
    for item in paired["rows"]:
        result = item["arms"][arm]
        rows.append({"function": item["function"], "draft_sha256": item["draft_sha256"],
                     "sequence": {"compiled": result["compiled"], "exact": result["exact"],
                                  "frontend_passed": result["frontend"] == "passed"},
                     "diagnostic_trace": [{"after": "final", "errors": result["errors"]}]})
    arms[arm] = {"rows": rows}
    (OUT / f"ratchet-{arm}.json").write_text(json.dumps(arms[arm], indent=2) + "\n")
ratchet = compare(arms["before"], arms["after"])
histograms = {}
acceptance = {}
for arm in arms:
    results = [r["arms"][arm] for r in paired["rows"]]
    histograms[arm] = dict(Counter(k for r in results for k in r["classes"]))
    acceptance[arm] = {"ido": sum(r["compiled"] for r in results),
                       "ido_and_frontend": sum(r["compiled"] and r["frontend"] == "passed" for r in results),
                       "exact": sum(r["exact"] for r in results)}
summary = {"states": len(paired["rows"]), "acceptance": acceptance, "histograms": histograms,
           "quality_ratchet": ratchet, "unique_compiles": sum(r["unique_compiles"] for r in paired["rows"]),
           "seconds": paired["seconds"],
           "saved_baselines_reproduced": sum(r["before_reproduces_saved_final"] for r in paired["rows"]),
           "gains": [{"function": r["function"], "frontend": r["arms"]["after"]["frontend"],
                      "score": r["arms"]["after"]["score"]} for r in paired["rows"]
                     if r["arms"]["after"]["compiled"] and not r["arms"]["before"]["compiled"]]}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
assert ratchet["holds"]

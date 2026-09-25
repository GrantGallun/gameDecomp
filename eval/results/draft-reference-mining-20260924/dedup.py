"""Post-hoc robustness (after RESULT.md): collapse clone families before counting associations.

Clone key = (target instruction count, draft C-shape counter, reference C-shape counter). The switch association
turned out to rest on nine near-identical updateCharacterSelectCoursePreviewPanel{1..9} pairs, so every association
is recounted with one pair per clone key. Specific = lift >= 5 under the same n/k/p floors."""
import collections, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mine

pairs, _ = mine.mining_pairs()
rows = {json.loads(p.read_text())["function"]: json.loads(p.read_text()) for p in (mine.E / "rows").glob("*.json")}
seen, dedup = set(), []
for p in pairs:
    r = rows[p["function"]]
    key = (len(mine.mask(r["target_dump"])), tuple(sorted(mine.shape(r["draft_def"]).items())),
           tuple(sorted(mine.shape(r["ref_def"]).items())))
    if key not in seen:
        seen.add(key)
        dedup.append(p)
out = {}
for label, ps in (("all", pairs), ("dedup", dedup)):
    assoc, n_r = mine.associations(ps)
    out[label] = {"pairs": len(ps), "specific": sorted(
        [[r, e, a["k"], a["n_r"], round(a["p"], 3), round(a["lift"], 1)] for (r, e), a in assoc.items()
         if a["enriched"] and a["lift"] >= 5], key=lambda x: -x[5])}
a = json.loads((HERE / "analysis.json").read_text())
assoc, n_r = mine.associations(dedup)
enr = mine.enriched_by_feature(assoc)
cov = sum(any(f in enr and any(x[1]["lift"] >= 5 for x in enr[f]) for f in r["features"]) for r in a["population_rows"])
out["dedup_population_covered_any_specific"] = cov
(HERE / "dedup.json").write_text(json.dumps(out, indent=1))
print(out["all"]["pairs"], "->", out["dedup"]["pairs"], "pairs;", len(out["all"]["specific"]), "->",
      len(out["dedup"]["specific"]), "specific associations; population covered-any (specific, dedup):", cov)
for s in out["dedup"]["specific"]:
    print(s)

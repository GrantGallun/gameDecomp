"""Criterion 3: compiled derived candidates vs what the hidden mechanism achieved on the same parent."""
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
probes = [json.loads(l) for l in (HERE.parent / "population-transfer-20260922/probes.jsonl").read_text().splitlines()
          if l.strip() and '"derived:' in l]
best = collections.defaultdict(lambda: {"score": -1.0, "exact": False, "compiled": 0, "n": 0})
for p in probes:
    _, family, sha8, _i = p["label"].split(":", 3) if p["label"].count(":") >= 3 else (None, None, None, None)
    fam = p["label"].rsplit(":", 2)[0].split(":", 1)[1]
    sha8 = p["label"].rsplit(":", 2)[1]
    b = best[(p["function"], fam, sha8)]
    b["n"] += 1
    b["compiled"] += p["compiled"]
    if p["compiled"]:
        b["score"] = max(b["score"], p["score"])
    b["exact"] = b["exact"] or p["exact"]
rows = json.loads((HERE / "derive.json").read_text())
merged = {}                                   # a parent can have several hidden-mechanism children: keep the best
for r in rows:
    key = (r["function"], r["hidden"], r["parent_sha"][:8])
    m = merged.setdefault(key, dict(r))
    m["hidden_child_exact"] = m["hidden_child_exact"] or r["hidden_child_exact"]
    m["hidden_child_score"] = max(m["hidden_child_score"], r["hidden_child_score"])
summary = collections.defaultdict(collections.Counter)
for key, r in merged.items():
    s = summary[r["hidden"]]
    s["parents"] += 1
    s["hidden_exact"] += r["hidden_child_exact"]
    b = best.get(key)
    if not b:
        continue
    s["compiled_candidates"] += b["compiled"]
    s["candidates"] += b["n"]
    s["derived_improves_parent"] += b["score"] > r["parent_score"] or b["exact"]
    s["derived_matches_or_beats_hidden"] += b["exact"] or (not r["hidden_child_exact"] and b["score"] >= r["hidden_child_score"])
    s["hidden_exact_reproduced"] += r["hidden_child_exact"] and b["exact"]
out = {k: dict(v) for k, v in summary.items()}
(HERE / "score.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))

"""Coverage when the deterministic tool and the model are combined (each case counted once)."""
import collections
import json

import run

tool = {t["id"] for t in map(json.loads, open(run.E / "tool.jsonl")) if t["exact"]}
model = collections.defaultdict(set)
for a in map(json.loads, open(run.E / "attempts_localized.jsonl")):
    if a["status"] == "exact":
        model[a["level"]].add(a["id"])
ids = [c["id"] for c in map(json.loads, open(run.E / "cases.jsonl"))]
cls = {i: i.split(":")[0] for i in ids}
anymodel = set().union(*model.values())
print("tool", len(tool), "| tool+RS", len(tool | model["RS"]), "| tool+R3", len(tool | model["R3"]),
      "| tool+any real-info model level", len(tool | anymodel), "of", len(ids))
rest = [i for i in ids if i not in tool | anymodel]
print("unsolved by anything (real information only):", dict(collections.Counter(cls[i] for i in rest)))

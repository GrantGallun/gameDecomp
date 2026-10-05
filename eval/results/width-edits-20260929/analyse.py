"""Summarise results.jsonl against the pre-registered measures."""
import json, collections, sys
from pathlib import Path
rows = [json.loads(l) for l in (Path(__file__).with_name("results.jsonl")).read_text().splitlines() if l.strip()]
bands = collections.defaultdict(collections.Counter)
declines = collections.Counter()
for r in rows:
    b = bands[r["band"]]; a = bands["all"]
    for c in (b, a):
        c["n"] += 1
        c["error"] += bool(r.get("error"))
        c["exact"] += bool(r.get("exact"))
        c["fired (width edit compiled)"] += r["width_edits_tried"] > 0
        base, low = r["extension_units_baseline"], r["extension_units_min_child"]
        c["acted on class (ext units fell)"] += base is not None and low is not None and low < base
        c["ext cleared to 0"] += base and low == 0
        c["score improved"] += (r.get("best") or 0) > (r.get("baseline") or 0)
    if r["width_edits_tried"] == 0 and not r.get("error"):
        rec = r.get("level0_receipt") or {}
        declines[rec.get("declined") or ("no width edit among first 24 compiled" if rec.get("proposals") else "no receipt")] += 1
order = ["all", "small", "medium", "large+"]
keys = ["n", "error", "fired (width edit compiled)", "acted on class (ext units fell)", "ext cleared to 0", "score improved", "exact"]
print(f"{'':34s}" + "".join(f"{b:>9s}" for b in order))
for k in keys:
    print(f"{k:34s}" + "".join(f"{bands[b][k]:9d}" for b in order))
print("\nwhy no width edit was compiled:", declines.most_common())
print("\nexact:", [r["function"] for r in rows if r.get("exact")])
print("errors:", collections.Counter((r.get("error") or "")[:70] for r in rows if r.get("error")).most_common(4))

"""Print the non-register differing instruction pairs for functions carrying a label (reads diffs/, classes.json).

    python3 eval/results/ninety-census-20260914/samples.py LABEL_PREFIX [MAX_FUNCTIONS] [--single]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
classes = json.loads((HERE / "classes.json").read_text())
prefix = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 8
single = "--single" in sys.argv
with_source = "--source" in sys.argv
names = set(prefix[len("names="):].split(",")) if prefix.startswith("names=") else None
shown = 0
for row in classes["rows"]:
    if names is not None:
        if row["function"] not in names:
            continue
    elif not any(l.startswith(prefix) for l in row["labels"]):
        continue
    causes = {f for f in row["families"] if f != "register"}
    if single and len(causes) != 1:
        continue
    entry = json.loads((HERE / "diffs" / f"{row['function']}.json").read_text())
    print(f"\n## {row['function']} score={row['score']} n={row['instructions']} labels={row['labels'][:10]} cert={row['certificate']} boundary={row['boundary']}")
    if with_source:
        import re
        text = entry["source_text"]
        start = re.search(rf"(?m)^[^\n;#]*\b{re.escape(row['function'])}\s*\([^;]*$", text)
        print(text[start.start():][:1800] if start else text[-1800:])
    for d in (entry.get("compare") or {}).get("differences", [])[:14]:
        if "kind" not in d:
            continue
        print(f"   {d['kind']:8} @{d.get('index')}  -{' | '.join(d['target'])[:90]:90}  +{' | '.join(d['candidate'])[:90]}")
    shown += 1
    if shown >= limit:
        break

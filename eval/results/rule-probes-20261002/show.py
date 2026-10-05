"""Print the differing listings of chosen probe rows."""
import json
import sys
from pathlib import Path

data = json.loads((Path(__file__).parent / "probes.json").read_text())
fam, ctx = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None
for row in data["families"][fam]:
    if (ctx is None or row["context"] == ctx) and row["listings"]:
        print("==", fam, row["opt"], row["context"])
        a, b = row["pair"]
        la, lb = row["listings"][a], row["listings"][b]
        for i in range(max(len(la), len(lb))):
            x = la[i] if i < len(la) else ""
            y = lb[i] if i < len(lb) else ""
            print(f"  {'*' if x != y else ' '} {x:34} | {y}")

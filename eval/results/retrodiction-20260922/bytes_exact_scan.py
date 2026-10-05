"""Read-only: recorded nodes whose object certificate is exact but whose source failed the frontend gate."""
import collections
import json
from pathlib import Path

ROOTS = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
         Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows",
         Path.home() / "decomp/experiments/measured-potential-20260922/rows",
         Path.home() / "decomp/experiments/retrodiction-20260922/search/rows"]
hits = collections.defaultdict(list)
for root in ROOTS:
    for path in sorted(root.glob("*.json")):
        row = json.loads(path.read_text())
        if not row.get("world") or not Path(row["world"]).exists():
            continue
        for n in json.loads(Path(row["world"]).read_text())["world"]["nodes"]:
            v = n["verdict"]
            cert = v.get("verification") or {}
            if v["compiled"] and not v["exact"] and cert.get("exact") is True:
                diag = ((v.get("frontend") or {}).get("diagnostics") or "").splitlines()
                first = next((l for l in diag if "error" in l), "")
                hits[row["function"]].append({"world": row["world"], "id": n["id"], "error": first.split("error:", 1)[-1].strip()[:140]})
print(len(hits), "functions with a byte-exact but frontend-rejected candidate")
for f, h in sorted(hits.items()):
    print(f"  {f}: {len(h)} nodes; {h[0]['error']}")
Path("/mnt/c/Code/gameDecomp/eval/results/retrodiction-20260922/bytes-exact-frontend-rejected.json").write_text(
    json.dumps({f: h[:3] for f, h in hits.items()}, indent=1))

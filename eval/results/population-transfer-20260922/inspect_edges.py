"""Read-only: stop reasons, and what residual_evidence edges change (why 100% improve, 0 exact)."""
import collections, json, sys
from pathlib import Path
NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
rows = [json.loads(p.read_text()) for p in sorted((NATIVE / "rows").glob("*--expanded.json"))]
print("stop reasons (expanded, non-exact baseline):", collections.Counter(r.get("stop") for r in rows if not r.get("baseline_exact")))
print("zero-variant roots:", [(r["function"], r["insn_count"], r["baseline_score"]) for r in rows if r.get("root_census") == {}])
shown = 0
for r in rows:
    w = json.loads(Path(r["world"]).read_text())["world"] if r.get("world") else None
    if not w:
        continue
    nodes = {n["id"]: n for n in w["nodes"]}
    for n in w["nodes"]:
        if n["family"] == "residual_evidence" and shown < int(sys.argv[1] if len(sys.argv) > 1 else 3):
            shown += 1
            p = nodes[n["parent"]]
            print("=" * 70, "\n", r["function"], n["label"], p["verdict"]["score"], "->", n["verdict"]["score"])
            import difflib
            print("".join(list(difflib.unified_diff(p["source"].splitlines(True), n["source"].splitlines(True), n=1))[2:40]))
            print("REMAINING DIFF:\n", "\n".join((n["verdict"].get("diff") or "").splitlines()[2:30]))

"""Independently recompile, then record, exacts produced by the fixed mechanisms. Ratchet-checked."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate  # noqa: E402
from eval.search_evolution import compile_logged  # noqa: E402

NATIVE = Path.home() / "decomp/experiments/machinery-capability-20260922"
REPO = Path.home() / "decomp/sbk1"


def main():
    probes = json.loads((HERE / "probes-fixed.json").read_text())
    outcomes = [json.loads(l) for l in (PT / "probes.jsonl").read_text().splitlines() if l.strip()]
    exact = {o["source_sha256"] for o in outcomes if o["exact"] and o["label"].startswith("fixed:")}
    chosen = {}
    for p in probes:
        sha = hashlib.sha256(p["source"].encode()).hexdigest()
        if sha in exact:
            chosen.setdefault(p["function"], (sha, p))
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    before = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    for name, (sha, p) in sorted(chosen.items()):
        if conn.execute("SELECT 1 FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1",
                        (name,)).fetchone():
            results.append({"function": name, "status": "already-object-exact"})
            continue
        tier = "project-header-assisted" if '#include "game/' in p["source"] else "source-independent"
        repo = isolate(REPO, NATIVE / "inventory" / name, name)
        verdict = compile_logged(repo / "nonmatchings" / name, repo, name, p["source"], conn=conn,
            strategy=f"machinery-capability-20260922:{tier}", run_id="machinery-capability-inventory-20260922",
            action="independent-confirmation", model="deterministic-repair", run_kind="machinery-repair",
            prompt="Fresh recompile of a candidate from a repaired mechanism; no reference body.",
            extra={"training_eligible": False, "assistance_tier": tier, "mechanism": p["label"]})
        results.append({"function": name, "source_sha256": sha, "exact": verdict["exact"],
                        "receipt_id": verdict["receipt_id"], "assistance_tier": tier, "status": "new-object-exact"})
        assert verdict["exact"], name
    after = {r[0] for r in conn.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"results": results, "before": len(before), "after": len(after)}
    (HERE / "inventory-receipts.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

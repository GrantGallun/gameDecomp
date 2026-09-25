"""Record operand-repair results into the production KB (PROTOCOL.md "Recording"). Ratchet-checked.

Each exact row's source is rescored in a FRESH isolated repo through the main-tree `solver.workspace.score` (the
certificate with the 2026-09-24/25 stages) with the production KB connection; recorded only when
`workspace.repair_complete` holds (certificate exact and the frontend gate passed or absent).
Tier: `project-header-assisted` if the source includes `game/` headers, else `source-independent` (eval.status applies
its reference-type test on top). Every function here is campaign-unmatched, so each confirmation is new to the project.

    python3 record.py -> receipts.json
"""
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate  # noqa: E402
from solver import workspace  # noqa: E402

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
E = H / "experiments/operand-repair-20260925"
CAMP = H / "runs/resume-pipeline-20260908/campaign.sqlite"


def main():
    camp = sqlite3.connect(f"file:{CAMP}?mode=ro", uri=True)
    camp_exact = {n for (n,) in camp.execute("select distinct f.name from attempts a join functions f "
                                             "on f.addr=a.func_addr where a.exact=1")}
    kb = sqlite3.connect(H / "kb-sbk1.sqlite", timeout=300)
    before = {r[0] for r in kb.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results = []
    recorded = {n for (n,) in kb.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                         "where a.exact=1 and a.run_id='operand-repair-record-20260925'")}
    rows_dir = sys.argv[sys.argv.index("--rows") + 1] if "--rows" in sys.argv else "rows"
    for path in sorted((E / rows_dir).glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("status") not in ("exact", "exact-at-baseline") or not row.get("source"):
            continue
        name, source = row["function"], row["source"]
        if name in recorded:                               # already confirmed and recorded by an earlier run
            continue
        tier = "project-header-assisted" if '#include "game/' in source else "source-independent"
        repo = isolate(H / "sbk1", E / "record" / name, name)
        att = workspace.score(repo / "nonmatchings" / name, repo, name, source, conn=kb, func=name,
                              strategy=f"operand-repair-20260925:{tier}", run_id="operand-repair-record-20260925",
                              extra={"training_eligible": False, "assistance_tier": tier,
                                     "path": [s.get("label") for s in row.get("path", []) if "label" in s],
                                     "certificate_stages": "strict / same-addend HI-LO pairing / rodata values"})
        kb.commit()
        ok = workspace.repair_complete(att)
        v = att.verification or {}
        results.append({"function": name, "status": "recorded-exact" if ok else "not-confirmed", "tier": tier,
                        "receipt_id": att.receipt_id, "new_to_campaign": name not in camp_exact,
                        "pairing_normalized": v.get("relocation_pairing_normalized"),
                        "rodata_values_compared": v.get("rodata_values_compared")})
        print(results[-1], flush=True)
    after = {r[0] for r in kb.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    assert before <= after
    out = {"counts": dict(collections.Counter((r["status"], r["tier"]) for r in results).items()) and
           {f"{s}|{t}": n for (s, t), n in collections.Counter((r["status"], r["tier"]) for r in results).items()},
           "new_to_campaign": sum(r["new_to_campaign"] and r["status"] == "recorded-exact" for r in results),
           "results": results}
    (HERE / ("receipts.json" if rows_dir == "rows" else f"receipts-{rows_dir}.json")).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))


if __name__ == "__main__":
    main()

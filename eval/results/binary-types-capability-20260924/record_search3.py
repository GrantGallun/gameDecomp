"""Confirm and record search round 3's harness exacts through the main-tree scorer (current certificate + frontend
gate) into the production KB. Ratchet-checked; contamination screen as record.py."""
import json, sqlite3, sys
from pathlib import Path
ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from solver import workspace

H = Path.home() / "decomp"
E = H / "experiments/binary-types-capability-20260924"
HERE = Path(__file__).resolve().parent
kb = sqlite3.connect(H / "kb-sbk1.sqlite", timeout=300)
camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
camp_exact = {n for (n,) in camp.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr where a.exact=1")}
before = {r[0] for r in kb.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
out = []
for name in json.loads((HERE / "search3-summary.json").read_text())["exact_functions"]:
    source = json.loads((E / "search3" / f"{name}.json").read_text())["source"]
    if '#include "game/' in source:
        out.append({"function": name, "status": "screened-out"})
        continue
    repo = isolate(H / "sbk1", E / "record3" / name, name)
    att = workspace.score(repo / "nonmatchings" / name, repo, name, source, conn=kb, func=name,
                          strategy="binary-types-search3-20260924:source-independent", run_id="binary-types-search3-20260924",
                          extra={"training_eligible": False, "assistance_tier": "source-independent"})
    kb.commit()
    out.append({"function": name, "status": "recorded-exact" if workspace.repair_complete(att) else "not-confirmed",
                "receipt_id": att.receipt_id, "new_to_campaign": name not in camp_exact,
                "verification": (att.verification or {}).get("status"), "frontend": (att.frontend or {}).get("passed")})
after = {r[0] for r in kb.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
assert before <= after
(HERE / "inventory-receipts-search3.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))

"""How do the binary-types confirmations relate to the campaign's own ledger (the dashboard's numbers)? Read-only.

Campaign: runs/resume-pipeline-20260908/campaign.sqlite (attempts.exact) and campaign.json states.
KB: kb-sbk1.sqlite, binary-types* run_ids with exact=1 and the frontend gate passed."""
import collections, json, sqlite3
from pathlib import Path
H = Path.home() / "decomp"
camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
kb = sqlite3.connect(f"file:{H / 'kb-sbk1.sqlite'}?mode=ro", uri=True)
camp_exact = {n for (n,) in camp.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr where a.exact=1")}
ours = set()
for name, sampling in kb.execute("select f.name, a.sampling from attempts a join functions f on f.addr=a.func_addr "
                                 "where a.exact=1 and a.run_id like 'binary-types%'"):
    fe = (json.loads(sampling) or {}).get("frontend") if sampling else None
    if fe and fe.get("passed"):
        ours.add(name)
kb_exact_other = {n for (n,) in kb.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                           "where a.exact=1 and (a.run_id is null or a.run_id not like 'binary-types%')")}
state = json.loads((H / "runs/resume-pipeline-20260908/campaign.json").read_text())
print("campaign exact functions:", len(camp_exact))
print("binary-types confirmed (gate passed):", len(ours))
print("  already exact in campaign:", len(ours & camp_exact))
print("  NEW to the campaign:", len(ours - camp_exact))
print("  already exact in KB by other routes (before today):", len(ours & kb_exact_other))
print("union campaign + binary-types:", len(camp_exact | ours))
out = {"campaign_exact": len(camp_exact), "ours": len(ours), "overlap": len(ours & camp_exact),
       "new_to_campaign": sorted(ours - camp_exact), "union": len(camp_exact | ours)}
Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=1))

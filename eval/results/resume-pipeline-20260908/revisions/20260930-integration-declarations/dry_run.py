"""Dry run: integration sweeps with the reviewed prepare_integration.py against COPIES of the current state and ledger.

The copied frozen tree has only eval/prepare_integration.py replaced. Every pending node's "attempted" evidence key is
cleared IN MEMORY so each is tried once under the new preparer (live, the amendment's pin change does this by itself).
Sweeps repeat until one selects nothing new. Nothing is saved; the result is written as JSON.
"""
import collections, json, sys, time
from pathlib import Path
D = Path.home() / "decomp" / "runs" / "integration-declarations-dryrun-20260930"
sys.path.insert(0, str(D / "code"))
from eval import campaign_integration, campaign_state  # noqa: E402
REPO = Path.home() / "decomp" / "sbk1"
state = campaign_state.read(D / "state" / "campaign.json")
assert not state.get("fast_inflight"), "live copy has in-flight work"
before = collections.Counter(n["status"] for n in state["nodes"].values())
state.setdefault("integration_sweep", {}).setdefault("attempted", {}).clear()
sessions, t0 = [], time.time()
for k in range(8):
    changed = campaign_integration.sweep(state, repo=REPO, db=D / "state" / "campaign.sqlite",
                                         artifacts=D / "artifacts", checkpoint=None)
    latest = state["integration_sweep"]["latest"]
    c = collections.Counter(n["status"] for n in state["nodes"].values())
    sessions.append({"selected": latest.get("selected"), "status": latest.get("status"), "error": latest.get("error"),
                     "changed": changed, "integrated": c["integrated"], "pending": c["function_exact_pending_integration"],
                     "records": [{k2: r.get(k2) for k2 in ("status", "functions", "error")} for r in latest.get("records", [])
                                 if len(r.get("functions") or []) == 1]})
    print(k, latest.get("status"), latest.get("selected"), "integrated", c["integrated"], "pending",
          c["function_exact_pending_integration"], flush=True)
    if not changed or not latest.get("selected"):
        break
after = collections.Counter(n["status"] for n in state["nodes"].values())
out = {"seconds": round(time.time() - t0), "before": dict(before), "after": dict(after), "sessions": sessions}
(D / "dry-run-result.json").write_text(json.dumps(out, indent=1, default=str))
print("integrated", before["integrated"], "->", after["integrated"])

"""Dry run of the reviewed integration sweep against COPIES of the campaign state and ledger.

Runs from a copy of the frozen campaign code with only eval/campaign_integration.py replaced by the reviewed
version. Nothing live is read for writing: state and ledger are copies under the dry-run directory, the sweep's
builds are isolated as always, and the resulting state is NOT saved anywhere (the result is written as JSON).

    cd ~/decomp/runs/integration-recertify-dryrun-20260929/code && python3 <this file>
"""
import json
import sys
import time
from pathlib import Path

D = Path.home() / "decomp" / "runs" / "integration-recertify-dryrun-20260929"
sys.path.insert(0, str(D / "code"))
from eval import campaign_integration, campaign_state  # noqa: E402

REPO = Path.home() / "decomp" / "sbk1"
state = campaign_state.read(D / "state" / "campaign.json")
# The copied checkpoint holds 3 jobs interrupted by the 2026-09-29 WSL shutdown (all on pending functions outside the
# integration set). The live controller recovers them before any sweep; this in-memory copy just drops them so the
# drained-workers guard can pass. Recorded in the result.
interrupted = [j.get("function") for j in state.get("fast_inflight") or []]
state["fast_inflight"] = []
before = {n: v["status"] for n, v in state["nodes"].items()
          if v["status"] in ("integrated", "function_exact_pending_integration")}
started = time.time()
changed = campaign_integration.sweep(state, repo=REPO, db=D / "state" / "campaign.sqlite",
                                     artifacts=D / "artifacts", checkpoint=None)
hist = state.get("integration_sweep", {})
latest = hist.get("latest", {})
after = {n: state["nodes"][n]["status"] for n in before}
out = {
    "seconds": round(time.time() - started, 1),
    "dropped_interrupted_jobs_in_memory": interrupted,
    "changed": changed,
    "recertifications": (hist.get("recertifications") or [{}])[-1],
    "latest": {k: latest.get(k) for k in ("status", "error", "selected", "verified_union", "records")},
    "status_changes": {n: [before[n], after[n]] for n in before if before[n] != after[n]},
}
(D / "dry-run-result.json").write_text(json.dumps(out, indent=1, default=str))
rec = out["recertifications"].get("records", [])
print("seconds", out["seconds"], "changed", len(changed))
print("recertified", sum(r.get("result") == "recertified" for r in rec), "failed",
      [(r["function"], r.get("error")) for r in rec if r.get("result") == "failed"])
print("sweep status", latest.get("status"), "error", latest.get("error"), "selected", latest.get("selected"))
print("verified union", len(latest.get("verified_union") or []), "status changes", out["status_changes"])

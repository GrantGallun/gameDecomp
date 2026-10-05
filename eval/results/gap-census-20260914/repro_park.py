"""Reproduce a parking ValueError with a traceback: a parked node's own source, frontier and profile, zero model calls.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/repro_park.py FUNCTION [PROFILE]

Frozen campaign code, isolated workspace, private KB copy. Prints the traceback if it raises.
"""
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import agentrepair, campaign_state, campaign_workers, completion_campaign as campaign  # noqa: E402

function = sys.argv[1]
state = campaign_state.read(RUN / "campaign.json")
node = state["nodes"][function]
profile_name = sys.argv[2] if len(sys.argv) > 2 else node["jobs"][-1]["profile"]
profile = next((p for p in campaign.PROFILES if p["name"] == profile_name),
               {"name": profile_name, "model": False, "deterministic_budget": 0})
print("profile", profile, "retained", [r.get("attempt_id") for r in campaign.retained_candidates(node)])
native = Path(tempfile.mkdtemp(prefix="repro-park-"))
try:
    repo = campaign_workers.isolate(Path.home() / "decomp/sbk1", native / "game", function)
    db = native / "kb.sqlite"
    shutil.copy2(RUN / "campaign.sqlite", db) if "--campaign-db" in sys.argv else \
        shutil.copy2(Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite", db)
    source = Path(node["source"]).read_text()
    try:
        agentrepair.run(repo=repo, db=db, function=function, source=source, source_parent_attempt_id=None,
                        out=native / "receipt.json", best_source_out=native / "best.c", model="none",
                        endpoint="http://127.0.0.1:9", draws=1, depth=4, beam=3, max_calls=0, timeout=10, think="low",
                        num_thread=4, temperature=0.35, num_predict=100, seed=1, cache_dir=None, verbose=False,
                        retained_frontier=(), deterministic_budget=profile.get("deterministic_budget", 0),
                        structured_output=True, retry_invalid=True, include_header_context=True,
                        type_transaction=profile.get("type_transaction", False), resilient=True)
        print("completed without error")
    except Exception:
        traceback.print_exc()
finally:
    shutil.rmtree(native, ignore_errors=True)

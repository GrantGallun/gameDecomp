"""Read the native checkpoint and current scheduler without changing them."""
import collections
import json
from pathlib import Path
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parent / "resume-pipeline-20260908" / "code"
STATE = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json")


def main():
    sys.path.insert(0, str(FROZEN))
    from eval import campaign_state, completion_campaign
    from solver import repair_queue
    pointer = STATE.read_bytes()
    state = campaign_state.read(STATE)
    queue, selected = repair_queue.project(state, completion_campaign.PROFILES)
    work = queue["work_items"]
    profiles = collections.Counter(item["profile"].split("@")[0] for item in work.values())
    pending = {name for name, node in state["nodes"].items() if node["status"] == "pending"}
    with sqlite3.connect(f"file:{state['config']['db']}?mode=ro", uri=True) as conn:
        attempts = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        exact_attempt_functions = conn.execute("SELECT COUNT(DISTINCT func_addr) FROM attempts WHERE exact=1").fetchone()[0]
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    receipt = {
        "checkpoint": json.loads(pointer)["commit"],
        "nodes": len(state["nodes"]),
        "states": dict(collections.Counter(node["status"] for node in state["nodes"].values())),
        "eligible": len(work), "profiles": dict(sorted(profiles.items())),
        "pending_without_profile": len(pending - work.keys()),
        "next": selected,
        "next_ten": sorted(work.values(), key=lambda item: item["priority"])[:10],
        "attempts": attempts, "raw_exact_attempt_functions": exact_attempt_functions,
        "database_quick_check": integrity,
        "native_pause": (STATE.parent / "service.pause").exists(),
        "control_pause": (FROZEN.parent / "service.pause").exists(),
        "fast_inflight": len(state.get("fast_inflight", [])),
        "last_session": state.get("fast_metrics", {}).get("last_session"),
    }
    if pointer != STATE.read_bytes():
        raise RuntimeError("checkpoint moved during read-only audit")
    (HERE / "after-campaign.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "next_ten"}, indent=2))


if __name__ == "__main__":
    main()

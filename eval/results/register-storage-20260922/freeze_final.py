"""Freeze tested repair code before generating or inspecting follow-up drafts."""
import hashlib
import json
from pathlib import Path
import shutil

SHARED = Path(__file__).resolve().parents[3]
BASE = Path.home() / "decomp/experiments/search-evolution-20260922/code-diagnosis-v1"
DEST = Path.home() / "decomp/experiments/register-storage-20260922/code-v3"
OVERLAYS = ["solver/storage_repairs.py", "solver/regalloc_mutations.py"]


def main():
    assert not DEST.exists()
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    for relative in OVERLAYS:
        (DEST / relative).write_bytes((SHARED / relative).read_bytes())
    fixed = sorted((DEST / "solver").glob("*.py")) + [DEST / relative for relative in (
        "eval/search_evolution.py", "eval/search_scheduler.py", "eval/search_replay.py",
        "eval/campaign_workers.py", "eval/budget_ledger.py", "eval/tool_agent_run.py")]
    manifest = {"base": str(BASE), "code_root": str(DEST), "overlays": OVERLAYS,
        "files": {str(p.relative_to(DEST)): hashlib.sha256(p.read_bytes()).hexdigest() for p in fixed},
        "training_eligible": False, "followup": ["osStopThread", "osDestroyThread", "osStartThread", "osVirtualToPhysical",
            "osViSwapBuffer", "osViSetMode", "osCreateMesgQueue", "osPiGetCmdQueue"],
        "budget_per_arm": 32, "scheduler": {"name": "depth-1.18754", "mode": "depth", "quantum": 1.18754},
        "claim_scope": "ordering and namespace correction; repeat of exposed v2 panel, not fresh transfer"}
    (DEST / "storage-snapshot.json").write_text(json.dumps(manifest, indent=2))
    (Path(__file__).parent / "freeze-v3.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"code_root": str(DEST), "fixed_files": len(fixed), "followup": manifest["followup"]}))


if __name__ == "__main__":
    main()

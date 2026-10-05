"""Remove four campaign copies verified 2026-09-22 as byte-identical duplicates of the live WSL run.

Verification (read-only, before this script): every row of attempts, model_proposals, attempt_edges and
attempt_runs in both old campaign.sqlite copies is identical in the live
~/decomp/runs/resume-pipeline-20260908/campaign.sqlite; every commit of both old state stores is in
campaign.state.pre-prune-20260922-195748.sqlite with an identical manifest, except the WSL copy's last
commit 23444, an abandoned save whose replacement 45 s later has 4,938 descendants. Nothing reads these
files: campaign_service.state_path_for follows launch.json --state (the WSL path) and the dashboard reads
only the campaign.json pointer. User-approved. The two full-history pre-prune backups are kept.
"""
import json
from pathlib import Path
import time

WSL = Path.home() / "decomp/runs/resume-pipeline-20260908"
WIN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
TARGETS = {  # path: size in bytes observed at verification time
    WIN / "campaign.sqlite": None,
    WIN / "campaign.state.sqlite": None,
    WSL / "campaign.sqlite.pre-20260920-wsl-state-relocation": None,
    WSL / "campaign.state.sqlite.pre-20260920-wsl-state-relocation": None,
}
KEEP = [WSL / "campaign.sqlite", WSL / "campaign.state.sqlite", WSL / "campaign.state.pre-prune-20260922-195748.sqlite",
        WIN / "campaign.state.pre-prune-20260913-141307.sqlite"]


def main():
    launch = json.loads((WIN / "launch.json").read_text())["command"]
    assert launch[launch.index("--db") + 1] == str(WSL / "campaign.sqlite")
    assert launch[launch.index("--state") + 1] == str(WSL / "campaign.json")
    assert (WIN / "service.pause").exists(), "campaign must stay paused"
    for path in KEEP:
        assert path.exists(), f"expected to keep {path}"
    removed = []
    for path in TARGETS:
        for suffix in ("-journal", "-wal", "-shm"):
            assert not Path(str(path) + suffix).exists(), f"{path.name}{suffix} present"
        size = path.stat().st_size
        path.unlink()
        removed.append({"path": str(path), "bytes": size})
        print(json.dumps(removed[-1]), flush=True)
    receipt = {"kind": "remove-verified-duplicates", "at": time.time(), "removed": removed,
               "freed_gb": round(sum(r["bytes"] for r in removed) / 1e9, 2), "kept": [str(p) for p in KEEP]}
    out = WIN / "campaign-prune" / f"{time.time_ns()}-remove-duplicates.json"
    out.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"freed_gb": receipt["freed_gb"], "receipt": out.name}))


if __name__ == "__main__":
    main()

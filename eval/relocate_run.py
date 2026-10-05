"""Repoint a copied run at its new directory, and prove every path it records still resolves.

The campaign could not checkpoint on `/mnt/c`: `os.replace` of a file the process itself holds open is
refused by DrvFs, so the worker died on every start. The fix is to run it from the WSL filesystem, and
that means a COPY rather than a move -- the state records absolute artifact paths
(`<run>/campaign-artifacts/...`) for every repair receipt, and leaving the original in place keeps
those readable instead of rewriting decades of receipts.

Only two things must change, and both live in the checkpoint's `config`:

  * `config.project` -- the run's own `code/` tree, which the controller reads and executes
  * `config.db`      -- the campaign database it opens

Everything else is content-addressed or repo-absolute and survives the copy untouched. This script
changes those two, then VERIFIES by resolving the paths the state actually references rather than
trusting that the edit was complete.

    python3 -m eval.relocate_run --run NEW_DIR [--artifacts-from OLD_DIR] [--check]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state                                        # noqa: E402


def artifact_paths(state: dict, limit: int = 400) -> list[str]:
    """Absolute paths the state points at, sampled across nodes."""
    found: list[str] = []
    for node in state["nodes"].values():
        for job in (node.get("jobs") or []):
            for key in ("source", "artifact", "receipt", "output"):
                value = job.get(key)
                if isinstance(value, str) and value.startswith("/"):
                    found.append(value)
        if len(found) >= limit:
            break
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--artifacts-from", type=Path, default=None,
                    help="the original directory whose artifacts must remain readable")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    run = args.run.resolve().expanduser()
    state_path = run / "campaign.json"
    state = campaign_state.read(state_path)
    config = state["config"]

    new_project, new_db = str(run / "code"), str(run / "campaign.sqlite")
    report = {"run": str(run), "project_before": config.get("project"),
              "project_after": new_project, "db_before": config.get("db"), "db_after": new_db}

    # Everything the state references must resolve AFTER the edit, from the new location or from the
    # original that stays behind. A path that resolves nowhere is a receipt the campaign can no longer
    # produce, and finding that here is cheaper than finding it mid-run.
    referenced = artifact_paths(state)
    missing = [p for p in referenced if not Path(p).exists()]
    report.update(referenced_sampled=len(referenced), referenced_missing=len(missing),
                  missing_sample=missing[:5])
    if missing and not args.artifacts_from:
        report["status"] = "refused-missing-artifacts"
        print(json.dumps(report, indent=2))
        return 1

    for path, label in ((Path(new_project), "project"), (Path(new_db), "db")):
        if not path.exists():
            report["status"] = f"refused-{label}-absent"
            report[f"{label}_expected"] = str(path)
            print(json.dumps(report, indent=2))
            return 2

    if args.check:
        report["status"] = "would-relocate"
        print(json.dumps(report, indent=2))
        return 0

    config["project"], config["db"] = new_project, new_db
    state.setdefault("runtime_amendments", []).append(
        {"kind": "run-relocation", "applied_at": time.time(),
         "from": str(Path(report["project_before"]).parent if report["project_before"] else ""),
         "to": str(run),
         "reason": "the campaign could not checkpoint on /mnt/c: os.replace of a file the process "
                   "holds open is refused by DrvFs, so the worker died on every start",
         "changed": ["config.project", "config.db"],
         "limits": "Paths only. Artifacts stay where they were and remain readable; no candidate, "
                   "oracle, certificate, ratchet, model identity or held-out set is touched."})
    campaign_state.Store(state_path).save(state)

    restored = campaign_state.read(state_path)
    report["project_readback"] = restored["config"]["project"]
    report["db_readback"] = restored["config"]["db"]
    report["nodes"] = len(restored["nodes"])
    report["model_digest_unchanged"] = restored["model_digest"] == state["model_digest"]
    ok = (report["project_readback"] == new_project and report["db_readback"] == new_db
          and report["model_digest_unchanged"] and report["nodes"] == len(state["nodes"]))
    report["status"] = "relocated" if ok else "FAILED-ROUND-TRIP"
    (run / "relocation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())

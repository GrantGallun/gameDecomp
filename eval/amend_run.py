"""Amend a paused run: refresh its pinned inputs and stage the current code tree, with a receipt.

`fast_campaign` refuses to resume with `frozen inputs changed; explicit amendment required`, and
`eval/pin_diff.py` says why: `added 0, removed 1834, changed 1086`, every one of them a per-workspace
`.compiler-<hash>.sh|.json`. Those files ARE the compile commands, so the run's refusal is correct —
the `do`-token ban removal (`eval/remove_do_ban.py`) rewrote 2,016 of them and
`solver/compiler_recipe.prepare` now deletes superseded siblings. Nothing about that is a drift the
guard should hide; it is a solver change and it is recorded as one here.

Two things are amended, and they are different in kind:

  * INPUTS   -- the repo's `nonmatchings/` workspace files. `fast_campaign` carries these over from the
                state rather than re-freezing them, so a vanished or rewritten draft only shows up as
                a pin mismatch. Recomputed from disk and recorded.
  * SOLVER   -- the run's own `code/` tree, replaced with the current tree so the campaign uses this
                session's fixes. The previous tree is kept under `revisions/`.

The model identity is re-verified before anything is written: staging code must never be a way to
change the model a run is pinned to.

    python3 -m eval.amend_run --run DIR [--check]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state, frozen_wavefront                          # noqa: E402
from eval import completion_campaign as campaign                           # noqa: E402

AMENDMENT_KIND = "solver-and-input-amendment"
TREE_DIRS = ("solver", "eval", "kb", "patterns", "tools", "miner", "tests")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--revision", default=None, help="revision name; defaults to a timestamp")
    args = ap.parse_args(argv)

    run = args.run.resolve()
    state_path = run / "campaign.json"
    lock = run / "campaign.lock"
    with campaign.campaign_lock(lock):
        state = campaign_state.read(state_path)
        config = state["config"]
        project, repo = Path(config["project"]), Path(config["repo"])

        # The model must not move as a side effect of staging code.
        live = frozen_wavefront.model_digest(config["endpoint"], config["model"])
        if live != state["model_digest"]:
            print(json.dumps({"status": "refused-model-differs", "recorded": state["model_digest"],
                              "live": live}, indent=2))
            return 1

        # PINS MUST BE COMPUTED AFTER STAGING. The first version computed them here, before the copy
        # loop below, so they described the OLD code tree; the resume then recomputed `_pins` over the
        # newly staged tree, saw a difference, and refused with the same `frozen inputs changed` the
        # amendment exists to clear. The pre-staging set is still what the DIFF is measured against --
        # that is the report of what changed -- but what gets recorded is the post-staging truth.
        before_staging = campaign._pins(project, repo)
        recorded = state["pins"]
        added = sorted(set(before_staging) - set(recorded))
        removed = sorted(set(recorded) - set(before_staging))
        changed = sorted(n for n in set(before_staging) & set(recorded)
                         if before_staging[n] != recorded[n])

        code_changed = []
        for name in TREE_DIRS:
            source_root = ROOT / name
            if not source_root.is_dir():
                continue
            for src in source_root.rglob("*"):
                if not src.is_file() or "__pycache__" in src.parts or "results" in src.parts:
                    continue
                rel = src.relative_to(ROOT)
                live_copy = project / rel
                if not live_copy.is_file() or file_hash(live_copy) != file_hash(src):
                    code_changed.append(str(rel))

        summary = {"run": str(run), "pins_added": len(added), "pins_removed": len(removed),
                   "pins_changed": len(changed), "code_files_differing": len(code_changed),
                   "model_identical": True}
        if args.check:
            summary["status"] = "would-amend"
            summary["code_sample"] = code_changed[:20]
            print(json.dumps(summary, indent=2))
            return 0

        revision = run / "revisions" / (args.revision or time.strftime("amend-%Y%m%d-%H%M%S"))
        revision.mkdir(parents=True, exist_ok=False)
        shutil.copytree(project, revision / "previous-code")
        campaign_state.atomic(revision / "previous-state.json",
                              {"store": state_path.name, "commit": state.get("commit"),
                               "note": "previous pins and code; restore together only while paused"})

        staged = 0
        for name in TREE_DIRS:
            source_root = ROOT / name
            if not source_root.is_dir():
                continue
            for src in source_root.rglob("*"):
                if not src.is_file() or "__pycache__" in src.parts or "results" in src.parts:
                    continue
                dest = project / src.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                staged += 1

        state["pins"] = campaign._pins(project, repo)   # post-staging: the tree the run will actually read
        record = {"kind": AMENDMENT_KIND, "applied_at": time.time(),
                  "staged_files": staged, "revision": str(revision),
                  "pins": {"added": len(added), "removed": len(removed), "changed": len(changed)},
                  "reason": "the compiler recipes were rewritten on disk by the do-ban removal, and the "
                            "run's code tree predates this session's solver fixes",
                  "model_digest": state["model_digest"],
                  "limits": "No candidate is imported, no oracle, certificate, ratchet or held-out set "
                            "is touched, and the model identity is re-verified before writing."}
        state.setdefault("runtime_amendments", []).append(record)
        campaign_state.Store(state_path).save(state)

        restored = campaign_state.read(state_path)
        summary["round_trip_pins_match"] = restored["pins"] == state["pins"]
        summary["round_trip_model"] = restored["model_digest"] == state["model_digest"]
        summary["nodes"] = len(restored["nodes"])
        if not summary["round_trip_pins_match"] or not summary["round_trip_model"]:
            summary["status"] = "FAILED-ROUND-TRIP"
            print(json.dumps(summary, indent=2))
            return 2
        campaign_state.atomic(revision / "amendment.json", record)
        summary["status"] = "amended"
        summary["revision"] = str(revision)
        print(json.dumps(summary, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

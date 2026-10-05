"""Report exactly which pinned inputs differ from what a run recorded.

`fast_campaign` refuses to resume with `frozen inputs changed; explicit amendment required`. That
message is correct and deliberately unhelpful: it does not say WHAT changed, and an amendment written
without that list is a guess. This prints the diff, split by the two halves the check actually
compares -- the run's own `code/` tree and the repo's `nonmatchings/` workspace files -- because they
mean different things:

  * a change under `code/` is a SOLVER change, and staging one is an amendment
  * a change under `nonmatchings/` is an INPUT change (a draft was rewritten), which the campaign
    should normally adopt rather than refuse

    python3 -m eval.pin_diff --run DIR
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state, frozen_wavefront                          # noqa: E402
from eval import completion_campaign as campaign                           # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    state = campaign_state.read(run / "campaign.json")
    recorded = state["pins"]
    project = Path(state["config"]["project"])
    repo = Path(state["config"]["repo"])

    current = campaign._pins(project, repo)
    # This mirrors `fast_campaign`: pins for the repo's nonmatchings files are carried over from the
    # state, because the campaign does not re-freeze its own workspace drafts mid-run.
    current.update(frozen_wavefront.file_hashes(
        [Path(p) for p in recorded if Path(p).is_relative_to(repo / "nonmatchings")]))

    added = sorted(set(current) - set(recorded))
    removed = sorted(set(recorded) - set(current))
    changed = sorted(n for n in set(current) & set(recorded) if current[n] != recorded[n])

    def bucket(names):
        return dict(collections.Counter(
            "code/" if f"/{project.name}/" in n or n.startswith(str(project)) else
            "nonmatchings/" if "nonmatchings" in n else "other" for n in names))

    report = {"run": str(run), "project": str(project), "repo": str(repo),
              "recorded_pins": len(recorded), "current_pins": len(current),
              "added": len(added), "removed": len(removed), "changed": len(changed),
              "added_by_area": bucket(added), "removed_by_area": bucket(removed),
              "changed_by_area": bucket(changed),
              "added_sample": [str(Path(n).name) for n in added[:15]],
              "changed_sample": [str(Path(n).name) for n in changed[:15]],
              "removed_sample": [str(Path(n).name) for n in removed[:15]]}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({**report, "added": added, "removed": removed,
                                        "changed": changed}, indent=2))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

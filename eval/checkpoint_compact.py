"""Convert a run's full `campaign.json` into the compact checkpoint the dashboard can read.

`eval.completion_campaign` persists its state with `agentrepair._atomic_json`, which writes the WHOLE
state — every node, every job — to `campaign.json`. `eval/progress_map.py` refuses anything whose
`kind` is not `campaign-checkpoint-index-v1`, so a run bootstrapped by `eval/scratch_run.py` serves
`/api/map` as HTTP 503 `map requires a compact campaign checkpoint`, and the treemap cannot be used to
watch it.

The compact form is `campaign_state.Store`, which writes a small pointer to `campaign.json` and stores
the node objects by hash in a sibling `campaign.state.sqlite`. That is how the live run works, and
`deploy.py:93` converts through the same call. `campaign_state.read` already understands both forms, so
a converted run still resumes normally — which is the property this script asserts before it commits.

The original is preserved as `campaign.full.json`; the conversion is not destructive.

    python3 -m eval.checkpoint_compact --run DIR [--check]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state                                        # noqa: E402


def compact(run: Path, check_only: bool = False) -> dict:
    path = run / "campaign.json"
    if not path.is_file():
        return {"run": str(run), "status": "no-campaign-json"}
    pointer = json.loads(path.read_bytes())
    already = pointer.get("kind") == campaign_state.KIND
    row = {"run": str(run), "kind_before": pointer.get("kind"),
           "bytes_before": path.stat().st_size, "already_compact": already}
    if already or check_only:
        row["status"] = "already-compact" if already else "would-convert"
        return row

    state = campaign_state.read(path)                 # understands both forms
    backup = run / "campaign.full.json"
    if not backup.exists():
        shutil.copy2(path, backup)
    campaign_state.Store(path).save(state)

    # The conversion is only safe if the pointer round-trips. Assert it here rather than discovering it
    # on the next resume: `read` must return the same node set and status counts it started with.
    restored = campaign_state.read(path)
    before_statuses = {n: v.get("status") for n, v in state["nodes"].items()}
    after_statuses = {n: v.get("status") for n, v in restored["nodes"].items()}
    after = json.loads(path.read_bytes())
    row.update(kind_after=after.get("kind"), bytes_after=path.stat().st_size,
               nodes_before=len(before_statuses), nodes_after=len(after_statuses),
               statuses_identical=before_statuses == after_statuses,
               summary=restored.get("summary"), backup=str(backup))
    if after.get("kind") != campaign_state.KIND:
        raise ValueError("conversion did not produce a compact checkpoint")
    if before_statuses != after_statuses:
        raise ValueError("round-trip changed node statuses; restore from " + str(backup))
    row["status"] = "converted"
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--check", action="store_true", help="report without converting")
    args = ap.parse_args(argv)
    row = compact(args.run.resolve(), check_only=args.check)
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

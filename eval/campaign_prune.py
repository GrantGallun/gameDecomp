"""Compact a campaign checkpoint store: keep recent and daily commits, verify, then swap.

Why: `campaign_state.Store` keeps every commit and every superseded node object
forever. On 2026-09-13 the store was 17.7 GB, and the checkpoint the pointer
names referenced 0.05 GB of it. Every reader (`campaign_state.read`,
`read_for_resume`, `Store.__init__`, `data_matching`, the dashboard map) loads
only the commit a pointer file names, and a save only needs that commit's
manifest plus `max(id)` for the next id. The disk filled and the campaign crashed
mid-save.

    python -m eval.campaign_prune RUN_DIR --keep-recent 100            # build + verify, no swap
    python -m eval.campaign_prune RUN_DIR --keep-recent 100 --swap     # then swap, keeping a backup

Guarantees:
- Kept commits keep their ids, manifests and parents; the commits every pointer
  file in RUN_DIR names are always kept.
- The compacted store is built beside the original, integrity-checked, and
  every pointer's state is hydrated from BOTH stores and compared byte for byte
  before any swap.
- The swap renames the original to a backup and never deletes it. Remove the
  backup yourself once the campaign has saved from the compacted store.
- Refuses while a `-journal`/`-wal` file exists (a writer or an interrupted save).

The campaign must be stopped: this takes no campaign lock.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import campaign_state

POINTERS = ("campaign.json", "checkpoint.previous.json")


def _pointers(run: Path) -> dict[str, dict]:
    found = {}
    for name in POINTERS:
        path = run / name
        if path.is_file():
            pointer = json.loads(path.read_bytes())
            if pointer.get("kind") == campaign_state.KIND:
                if pointer.get("store") != "campaign.state.sqlite":
                    raise ValueError(f"{name} names an unexpected store")
                found[name] = pointer
    if "campaign.json" not in found:
        raise ValueError("campaign.json is not a checkpoint-index pointer")
    return found


def select(commits: list[tuple[int, float]], keep_recent: int, required: set[int]) -> set[int]:
    """Ids to keep: newest `keep_recent`, the last commit of each local day, and `required`."""
    ordered = sorted(commits)
    keep = {cid for cid, _created in ordered[-keep_recent:]} if keep_recent else set()
    daily: dict[str, int] = {}
    for cid, created in ordered:
        daily[datetime.datetime.fromtimestamp(created).date().isoformat()] = cid
    return keep | set(daily.values()) | required


def _manifest_hashes(manifest: bytes) -> set[str]:
    data = json.loads(manifest)
    return {data["metadata"], *data["nodes"].values()}


def _hydrated(db: Path, pointer: dict) -> bytes:
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        return campaign_state.encode(campaign_state._hydrate(conn, pointer))


def build(run: Path, keep_recent: int) -> dict:
    run = run.resolve()
    source = run / "campaign.state.sqlite"
    for suffix in ("-journal", "-wal"):
        if Path(str(source) + suffix).exists():
            raise ValueError(f"{source.name}{suffix} exists: a writer is active or a save was interrupted")
    pointers = _pointers(run)
    target = run / ".campaign.state.compact.sqlite"
    if target.exists():
        raise ValueError(f"{target.name} already exists; inspect or remove it first")
    started = time.time()
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
        commits = old.execute("SELECT id, created FROM commits").fetchall()
        required = {p["commit"] for p in pointers.values()}
        keep = select(commits, keep_recent, required)
        with closing(sqlite3.connect(target)) as new:
            new.execute("PRAGMA synchronous=FULL")
            new.execute("CREATE TABLE objects(hash TEXT PRIMARY KEY,payload BLOB NOT NULL)")
            new.execute("CREATE TABLE commits(id INTEGER PRIMARY KEY,parent INTEGER,manifest BLOB NOT NULL,created REAL NOT NULL)")
            wanted: set[str] = set()
            for row in old.execute("SELECT id, parent, manifest, created FROM commits WHERE id IN "
                                   "(SELECT value FROM json_each(?)) ORDER BY id", (json.dumps(sorted(keep)),)):
                new.execute("INSERT INTO commits VALUES (?,?,?,?)", row)
                wanted |= _manifest_hashes(row[2])
            copied = 0
            ordered = sorted(wanted)
            for start in range(0, len(ordered), 500):
                chunk = ordered[start:start + 500]
                rows = old.execute("SELECT hash, payload FROM objects WHERE hash IN (SELECT value FROM json_each(?))",
                                   (json.dumps(chunk),)).fetchall()
                new.executemany("INSERT INTO objects VALUES (?,?)", rows)
                copied += len(rows)
            new.commit()
            if copied != len(wanted):
                raise ValueError(f"source store is missing {len(wanted) - copied} objects kept commits reference")
            if new.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("compacted store failed integrity_check")
            max_old = old.execute("SELECT max(id) FROM commits").fetchone()[0]
            if new.execute("SELECT max(id) FROM commits").fetchone()[0] != max_old:
                raise ValueError("compacted store would change the next commit id")
    verified = {}
    for name, pointer in pointers.items():
        a, b = _hydrated(source, pointer), _hydrated(target, pointer)
        if a != b:
            raise ValueError(f"{name}: hydrated state differs between stores")
        verified[name] = {"commit": pointer["commit"], "state_sha256": hashlib.sha256(a).hexdigest(), "bytes": len(a)}
    return {"kind": "campaign-store-prune", "run": str(run), "keep_recent": keep_recent,
            "commits_before": len(commits), "commits_kept": len(keep), "objects_kept": copied,
            "bytes_before": source.stat().st_size, "bytes_after": target.stat().st_size,
            "pointers_verified": verified, "seconds": round(time.time() - started, 1), "swapped": False}


def swap(run: Path, receipt: dict) -> dict:
    run = run.resolve()
    source, target = run / "campaign.state.sqlite", run / ".campaign.state.compact.sqlite"
    backup = run / f"campaign.state.pre-prune-{time.strftime('%Y%m%d-%H%M%S')}.sqlite"
    for suffix in ("-journal", "-wal"):
        if Path(str(source) + suffix).exists():
            raise ValueError("a writer appeared; not swapping")
    for name, check in receipt["pointers_verified"].items():
        if json.loads((run / name).read_bytes())["commit"] != check["commit"]:
            raise ValueError(f"{name} moved since verification; not swapping")
    source.rename(backup)
    try:
        target.rename(source)
    except OSError:
        backup.rename(source)                 # put the original back exactly as it was
        raise
    for name, check in receipt["pointers_verified"].items():
        state = campaign_state.encode(campaign_state.read(run / name))
        if hashlib.sha256(state).hexdigest() != check["state_sha256"]:
            raise ValueError(f"{name}: state after swap differs; original kept at {backup.name}")
    return {**receipt, "swapped": True, "backup": backup.name}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--keep-recent", type=int, default=100)
    parser.add_argument("--swap", action="store_true")
    args = parser.parse_args()
    receipt = build(args.run, args.keep_recent)
    if args.swap:
        receipt = swap(args.run, receipt)
    folder = args.run / "campaign-prune"
    folder.mkdir(exist_ok=True)
    campaign_state.atomic(folder / f"{time.time_ns()}.json", receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()

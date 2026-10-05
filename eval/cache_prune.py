"""Bound an `eval.fast_runtime` cache: evict least-recently-used entries until each kind fits a budget.

Why: nothing bounded the performance cache, and on 2026-09-21 one campaign worker's `cache/compile`
reached 141 GB (together with the campaign DB copies it filled C:, and WSL could not start). Measured
then: 4,316 of 278,248 compile entries (1.5%) had ever been read back. Every entry is a pure cache of
deterministic work keyed on its full inputs, so eviction costs a recompile and never a result.

Recency is max(atime, mtime) of `value.json`. Under `relatime` a read updates atime at most daily,
which is the resolution this needs. An entry whose lock another process holds is skipped, never
waited on, and eviction removes only `value.json` (the entry's directory and 0-byte lock stay), so
this is safe beside a running worker: `eval/campaign_hourly.ps1` runs it every hour.

    python -m eval.cache_prune ROOT [ROOT ...] --budget-gb 2            # report only
    python -m eval.cache_prune ROOT [ROOT ...] --budget-gb 2 --apply    # evict
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

KINDS = ("compile", "layouts", "semantic", "target-work")


def entries(root: Path, kind: str) -> list[tuple[float, int, bool, Path]]:
    """(last use, bytes, ever re-read, entry directory) for every entry of one kind."""
    found = []
    base = root / kind
    if not base.is_dir():
        return found
    for shard in base.iterdir():
        if not shard.is_dir():
            continue
        for directory in shard.iterdir():
            value = directory / "value.json"
            try:
                st = value.stat()
            except FileNotFoundError:
                continue
            found.append((max(st.st_atime, st.st_mtime), st.st_size, st.st_atime > st.st_mtime + 60, directory))
    return found


def _evict(directory: Path) -> bool:
    import fcntl
    lock = directory / "lock"
    try:
        handle = lock.open("a+b")
    except FileNotFoundError:
        handle = None
    try:
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False                              # in use by a worker: leave it
        # Only the value goes. The directory and its 0-byte lock stay, because a worker may already
        # hold the lock file open waiting for us; it must find a directory it can write its miss into.
        (directory / "value.json").unlink(missing_ok=True)
        return True
    finally:
        if handle is not None:
            handle.close()


def prune(root: Path, kind: str, budget: int, apply: bool) -> dict:
    items = sorted(entries(root, kind), key=lambda item: item[0], reverse=True)   # newest first
    total = sum(size for _t, size, _r, _d in items)
    kept, evict = 0, []
    for last, size, reread, directory in items:
        if kept + size <= budget:
            kept += size
        else:
            evict.append((size, directory))
    evicted = skipped = freed = 0
    if apply:
        for size, directory in evict:
            if _evict(directory):
                evicted += 1
                freed += size
            else:
                skipped += 1
    return {"root": str(root), "kind": kind, "entries": len(items), "bytes": total,
            "reread_entries": sum(r for _t, _s, r, _d in items),
            "over_budget_entries": len(evict), "over_budget_bytes": sum(s for s, _d in evict),
            "evicted": evicted, "skipped_locked": skipped, "freed_bytes": freed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="+", type=Path, help="cache roots, e.g. <worker>/cache")
    ap.add_argument("--budget-gb", type=float, required=True, help="per kind, per root")
    ap.add_argument("--kinds", default=",".join(KINDS))
    ap.add_argument("--apply", action="store_true", help="evict; without it only report")
    args = ap.parse_args(argv)
    budget = int(args.budget_gb * 1e9)
    rows = [prune(root, kind, budget, args.apply) for root in args.roots for kind in args.kinds.split(",")]
    for r in rows:
        print(json.dumps(r))
    freed = sum(r["freed_bytes"] for r in rows)
    over = sum(r["over_budget_bytes"] for r in rows)
    print(json.dumps({"applied": args.apply, "over_budget_gb": round(over / 1e9, 2), "freed_gb": round(freed / 1e9, 2),
                      "skipped_locked": sum(r["skipped_locked"] for r in rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

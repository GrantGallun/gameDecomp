"""Pack `source_attribution.instructions` inside `attempts.sampling`, losslessly, or unpack it again.

Why: every attempt's `sampling` JSON carried its per-instruction source attribution verbatim, ~30 KB a
row. On 2026-09-22 that was most of the 7.5 GB of `sampling` in the campaign DB, and the campaign keeps
four full copies of that DB (the main one and one per worker), so it was the largest remaining disk
cost after the compile-cache fix. The attribution is derived from the recorded compile, and no reader
takes it from the database: readers use `sampling.generation`, `compiler_recipe`, `verification` and
`run_id`. It is packed with `solver.source_attribution.pack_instructions`; read it back with
`solver.source_attribution.instructions_of`, which accepts both forms.

Lossless by check, not by argument: a row is rewritten only if unpacking the new text reproduces the
original text byte for byte (writers use `json.dumps(sampling, sort_keys=True)`). Any other row is left
exactly as it was and counted as `refused`. `--expand` is the inverse, with the mirrored check.

Batches are short transactions with a long busy timeout, so this is safe beside a running campaign. The
largest id examined is kept in `<db>.compact.json`, so a scheduled run only reads new rows. `--vacuum`
returns freed pages to the filesystem and needs the campaign stopped.

    python -m eval.attempt_compact DB                    # report only
    python -m eval.attempt_compact DB --apply [--vacuum]
    python -m eval.attempt_compact DB --apply --expand   # restore the plain form
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time

from solver import source_attribution as sa

PACK_FILTER = '%"instructions"%'        # any spelling; the transform decides and counts refusals
UNPACK_FILTER = '%"instructions_packed"%'


def pack_text(text: str) -> str | None | bool:
    """New text, None when there is nothing to pack, False when the round trip is not byte-identical."""
    obj = json.loads(text)
    attribution = obj.get("source_attribution")
    if not isinstance(attribution, dict) or not isinstance(attribution.get("instructions"), list) \
            or not attribution["instructions"]:
        return None
    packed = {**obj, "source_attribution": sa.pack_instructions(attribution)}
    restored = {**packed, "source_attribution": sa.unpack_instructions(packed["source_attribution"])}
    if json.dumps(restored, sort_keys=True) != text:
        return False
    return json.dumps(packed, sort_keys=True)


def unpack_text(text: str) -> str | None | bool:
    obj = json.loads(text)
    attribution = obj.get("source_attribution")
    if not isinstance(attribution, dict) or "instructions_packed" not in attribution:
        return None
    plain = {**obj, "source_attribution": sa.unpack_instructions(attribution)}
    new_text = json.dumps(plain, sort_keys=True)
    if pack_text(new_text) != text:
        return False
    return new_text


def _marker(db: Path) -> Path:
    return db.with_name(db.name + ".compact.json")


def run(db: Path, *, apply: bool, expand: bool = False, batch: int = 500, full: bool = False) -> dict:
    marker = _marker(db)
    since = 0 if (full or expand or not marker.exists()) else json.loads(marker.read_text()).get("max_id", 0)
    conn = sqlite3.connect(str(db), timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    transform = unpack_text if expand else pack_text
    pattern = UNPACK_FILTER if expand else PACK_FILTER
    top = conn.execute("SELECT COALESCE(MAX(id),0) FROM attempts").fetchone()[0]
    ids = [r[0] for r in conn.execute("SELECT id FROM attempts WHERE id > ? AND id <= ? AND sampling LIKE ? ORDER BY id",
                                      (since, top, pattern))]
    stats = {"db": str(db), "mode": "expand" if expand else "pack", "applied": apply, "since_id": since,
             "max_id": top, "candidates": len(ids), "rewritten": 0, "nothing_to_do": 0, "refused": 0,
             "refused_ids": [], "bytes_before": 0, "bytes_after": 0}
    for start in range(0, len(ids), batch):
        chunk = ids[start:start + batch]
        rows = conn.execute(f"SELECT id, sampling FROM attempts WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
        updates = []
        for identity, text in rows:
            new = transform(text)
            if new is None:
                stats["nothing_to_do"] += 1
            elif new is False:
                stats["refused"] += 1
                if len(stats["refused_ids"]) < 20:
                    stats["refused_ids"].append(identity)
            else:
                stats["rewritten"] += 1
                stats["bytes_before"] += len(text)
                stats["bytes_after"] += len(new)
                updates.append((new, identity, text))
        if apply and updates:
            with conn:                                   # one short transaction per batch
                for new, identity, old in updates:
                    # Guarded on the old text: a row changed since it was read is left for the next run.
                    conn.execute("UPDATE attempts SET sampling = ? WHERE id = ? AND sampling = ?", (new, identity, old))
    if apply and not expand:
        marker.write_text(json.dumps({"max_id": top, "at": time.time()}))
    conn.close()
    return stats


def vacuum(db: Path) -> dict:
    for suffix in ("-journal", "-wal"):
        if Path(str(db) + suffix).exists():
            raise RuntimeError(f"{db.name}{suffix} exists: a writer is active; stop the campaign first")
    before = db.stat().st_size
    conn = sqlite3.connect(str(db), timeout=300)
    conn.execute("VACUUM")
    ok = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    return {"bytes_before": before, "bytes_after": db.stat().st_size, "quick_check": ok}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db", type=Path)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expand", action="store_true", help="restore the plain form")
    ap.add_argument("--vacuum", action="store_true", help="after --apply; campaign must be stopped")
    ap.add_argument("--full", action="store_true", help="ignore the resume marker and examine every row")
    args = ap.parse_args(argv)
    result = run(args.db, apply=args.apply, expand=args.expand, full=args.full)
    if args.apply and args.vacuum:
        result["vacuum"] = vacuum(args.db)
    print(json.dumps(result))
    return 1 if result["refused"] and args.apply and not result["rewritten"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

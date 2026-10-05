"""Find rows whose compiles may have failed from the 2026-09-22 disk-full, and quarantine them for rerun.

A full disk can surface as an ordinary compiler refusal (the assembler cannot write the object), which
the verdict records as `compiled=False` with no infrastructure error. Any row whose world holds a
refusal mentioning no-space, or a missing or unreadable world, is moved aside so the resumable driver
reruns it. Quarantined rows are kept, not deleted.
"""
import json
from pathlib import Path
import sqlite3
import sys

NATIVE = Path.home() / "decomp/experiments/population-transfer-20260922"
MARKERS = ("No space left", "ENOSPC", "disk full", "Disk quota")


def main(apply: bool):
    db = sqlite3.connect(NATIVE / "attempts.sqlite", timeout=300)
    print("integrity:", db.execute("pragma integrity_check").fetchone()[0])
    print("attempts:", db.execute("select count(*) from attempts").fetchone()[0],
          "with no-space stderr:", db.execute(
              "select count(*) from attempts where " + " or ".join("compiler_stderr like ?" for _ in MARKERS),
              [f"%{m}%" for m in MARKERS]).fetchone()[0])
    bad = []
    for path in sorted((NATIVE / "rows").glob("*.json")):
        try:
            row = json.loads(path.read_text())
            world = json.loads(Path(row["world"]).read_text())["world"] if row.get("world") else None
        except Exception as exc:                                    # noqa: BLE001
            bad.append((path, f"unreadable: {type(exc).__name__}"))
            continue
        if world is None:
            continue
        for node in world["nodes"]:
            text = (node["verdict"].get("stderr") or "") + (node["verdict"].get("compiler_stderr") or "")
            if any(m in text for m in MARKERS):
                bad.append((path, f"no-space refusal at {node['id']}"))
                break
    print("affected rows:", len(bad))
    for path, why in bad:
        print(" ", path.name, why)
    if apply and bad:
        quarantine = NATIVE / "rows-quarantine"
        quarantine.mkdir(exist_ok=True)
        for path, _why in bad:
            path.rename(quarantine / path.name)
        print("quarantined", len(bad))


if __name__ == "__main__":
    main("--apply" in sys.argv)

"""Resume the interrupted context export into new files, preserving its measured prefix.

The old progress log has one ordered `admitted cumulative seconds` row per completed
function, including excluded functions. Verify those rows against the JSONL before
reusing any work. An infrastructure error excludes that function and retains the
diagnostic; it never supplies a negative compiler label.
"""
from __future__ import annotations

import collections
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import time


def key(row):
    return tuple(row[k] for k in ("repository", "variant", "file", "function"))


def validate_source_rows(retained, source):
    originals = {r["id"]: r for r in source}
    if len(originals) != len(source):
        raise ValueError("duplicate source ids")
    seen = set()
    for row in retained:
        original = originals.get(row["id"])
        if original is None or any(row.get(k) != v for k, v in original.items()):
            raise ValueError(f"context row {row['id']} disagrees with original source fields")
        if row["id"] in seen:
            raise ValueError("duplicate context ids")
        seen.add(row["id"])


def completed_prefix(log, groups, retained):
    progress = [(int(m[1]), int(m[2])) for line in log.splitlines()
                if (m := re.fullmatch(r"(\d+) (\d+) \d+s", line))]
    n = len(progress)
    if n > len(groups) or not n:
        raise ValueError("invalid completed group count")
    total = 0
    for admitted, cumulative in progress:
        total += admitted
        if total != cumulative:
            raise ValueError("inconsistent progress count")
    if total != len(retained) or len({r["id"] for r in retained}) != len(retained):
        raise ValueError("retained row count does not match completed progress")
    counts = collections.Counter(key(r) for r in retained)
    for (group, _), (admitted, _) in zip(groups[:n], progress):
        if counts[group] != admitted:
            raise ValueError("retained rows disagree with the completed group prefix")
    admitted_ids = {r["id"] for _, rows in groups[:n] for r in rows}
    if any(r["id"] not in admitted_ids for r in retained):
        raise ValueError("retained row does not belong to a completed group")
    validate_source_rows(retained, [r for _, rows in groups[:n] for r in rows])
    return n


def main():
    import context_tasks as ct
    import public_plant as pp

    public = ct.OUT
    inputs = [public / name for name in ("single.jsonl", "single_same.jsonl", "multi.jsonl")]
    source_rows = [json.loads(line) for p in inputs for line in p.read_text().splitlines()]
    groups = sorted(ct.by_function(source_rows).items(), key=lambda kv: hashlib.sha256(str(kv[0]).encode()).hexdigest())
    previous = public / "context.jsonl"
    previous_data = previous.read_bytes()
    retained = [json.loads(line) for line in previous_data.decode().splitlines()]
    old_log = (public / "context.log").read_text()
    prefix = completed_prefix(old_log, groups, retained)
    output = public / "context-v3.jsonl"
    if output.exists():
        raise SystemExit("recovery output already exists; no automatic overwriting")
    manifest = {"previous_sha256": hashlib.sha256(previous_data).hexdigest(),
                "previous_log_sha256": hashlib.sha256(old_log.encode()).hexdigest(),
                "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                "normalizer": pp.NORMALIZER, "retained_groups": prefix, "retained_rows": len(retained),
                "total_groups": len(groups), "new_infrastructure_errors": [],
                "prior_tally": "unavailable: previous process exited before writing its receipt"}
    (public / "context-v3.recovery.json").write_text(json.dumps(manifest, indent=2))
    (public / "context-v3.pid").write_text(str(os.getpid()))
    print(json.dumps({"retained_groups": prefix, "total_groups": len(groups), "retained_rows": len(retained)}), flush=True)
    builds = pp.builds()

    def work(item):
        group, rows = item
        try:
            got, tally = ct.work(group, rows, builds, 6)
            return got, dict(tally), None
        except Exception as exc:
            return [], {"infrastructure-error": 1}, {"group": group, "type": type(exc).__name__,
                        "error": str(exc), "stderr": getattr(exc, "stderr", None)}

    started, count = time.time(), len(retained)
    tally = collections.Counter()
    with output.open("wb") as out, (public / "context-v3.groups.jsonl").open("w") as journal:
        out.write(previous_data)
        out.flush()
        with concurrent.futures.ThreadPoolExecutor(8) as executor:
            for (group, _), (got, measured, error) in zip(groups[prefix:], executor.map(work, groups[prefix:])):
                for row in got:
                    out.write((json.dumps(row) + "\n").encode())
                out.flush()
                count += len(got)
                tally.update(measured)
                journal.write(json.dumps({"group": group, "rows": len(got), "cumulative": count,
                                          "tally": measured, "error": error}) + "\n")
                journal.flush()
                if error:
                    manifest["new_infrastructure_errors"].append(error)
                print(len(got), count, f"{time.time() - started:.0f}s", flush=True)
    manifest.update({"rows_admitted": count, "functions": len(groups), "new_tally": dict(tally),
                     "seconds": round(time.time() - started), "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest()})
    (public / "context-v3.receipt.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"complete": True, "rows_admitted": count, "new_tally": dict(tally)}), flush=True)


if __name__ == "__main__":
    main()

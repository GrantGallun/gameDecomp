"""Generate the project status from ground truth, so it cannot drift.

CLAUDE.md said "Design complete. No implementation yet." for weeks while 65
tests passed and 33 functions matched. An external review caught it. CLAUDE.md
is auto-loaded into every session, so a stale status makes every future agent
reason from a false premise -- the most expensive kind of documentation bug.

Numbers come from the KB, the filesystem and git. Nothing here is maintained by
hand.

    python3 -m eval.status            # markdown block for CLAUDE.md
    python3 -m eval.status --check    # non-zero if CLAUDE.md disagrees
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import subprocess
from pathlib import Path


def counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    q = conn.execute
    exact_db = {r[0] for r in q(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr where a.score >= 100")}
    # Candidates verified byte-exact but never logged, e.g. recovered from
    # permuter output. Counting only the DB under-reports; counting only files
    # over-reports. The union is the honest figure -- getting this wrong is how
    # 33 was reported as 34.
    on_disk = {os.path.basename(p)[:-2]
               for p in glob.glob("matched_recovered/*.c")}
    return {
        "exact": len(exact_db | on_disk),
        "exact_db_only": len(exact_db),
        "exact_disk_only": len(on_disk - exact_db),
        "attempted": q("select count(distinct func_addr) from attempts").fetchone()[0],
        "attempts": q("select count(*) from attempts").fetchone()[0],
        "evidence": q("select count(*) from evidence").fetchone()[0],
        "inference": q("select count(*) from inference").fetchone()[0],
    }


def tests() -> int:
    out = subprocess.run(["python3", "-m", "pytest", "tests/", "-q",
                          "--collect-only"], capture_output=True, text=True)
    for line in reversed(out.stdout.splitlines()):
        if "test" in line and "collected" in line:
            for tok in line.split():
                if tok.isdigit():
                    return int(tok)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path,
                    default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if CLAUDE.md's match count is stale")
    args = ap.parse_args()

    c = counts(args.db)
    n_tests = tests()

    print(f"| functions byte-exact | **{c['exact']}** of {c['attempted']} attempted |")
    print(f"| attempts logged | {c['attempts']:,} |")
    print(f"| evidence rows | {c['evidence']:,} |")
    print(f"| **inference rows** | **{c['inference']}** |")
    print(f"| tests | {n_tests} |")
    if c["exact_disk_only"]:
        print(f"\n({c['exact_disk_only']} verified match(es) exist only as files "
              f"in matched_recovered/ and are absent from the attempts table --"
              f" they were produced by a harness that did not log.)")

    if args.check:
        md = Path("CLAUDE.md").read_text(encoding="utf-8", errors="replace")
        if f"**{c['exact']}** of {c['attempted']} attempted" not in md:
            print(f"\nSTALE: CLAUDE.md does not state "
                  f"{c['exact']} of {c['attempted']}. Regenerate it.")
            return 1
        print("\nCLAUDE.md status is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

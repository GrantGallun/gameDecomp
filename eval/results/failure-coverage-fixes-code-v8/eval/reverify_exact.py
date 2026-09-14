"""Append verifier receipts for high-scoring historical attempts.

Older databases stored similarity scores but discarded the independent
byte-exact verdict.  This command recompiles those sources and appends new
attempt rows; it never guesses a verdict from score or edits historical rows.

    python3 -m eval.reverify_exact --db ~/decomp/kb-sbk1.sqlite \
        --repo ~/decomp/sbk1 --write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from kb import attempts as attempt_receipts
from solver import workspace


def legacy_candidates(conn: sqlite3.Connection, floor: float = 99.99,
                      per_function: int = 3) -> dict[str, list[tuple[int, float, str]]]:
    """Highest-scoring legacy sources whose exact verdict is still unknown."""
    attempt_receipts.ensure_exact_receipt(conn)
    already = attempt_receipts.exact_functions(conn)
    grouped: dict[str, list[tuple[int, float, str]]] = defaultdict(list)
    for name, aid, score, source in conn.execute(
            "SELECT f.name, a.id, a.score, a.source_code FROM attempts a "
            "JOIN functions f ON f.addr = a.func_addr "
            "WHERE a.compiled = 1 AND a.exact IS NULL AND a.score >= ? "
            "AND a.source_code IS NOT NULL ORDER BY f.name, a.score DESC, a.id",
            (floor,)):
        if name not in already and len(grouped[name]) < per_function:
            grouped[name].append((aid, score, source))
    return dict(grouped)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--floor", type=float, default=99.99)
    ap.add_argument("--per-function", type=int, default=3)
    ap.add_argument("--write", action="store_true",
                    help="compile and append receipts; without this, list only")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    conn = sqlite3.connect(str(args.db.expanduser()))
    repo = args.repo.expanduser()
    groups = legacy_candidates(conn, args.floor, args.per_function)
    print(f"{len(groups)} function(s) have legacy candidates at >= {args.floor}")
    for name, candidates in groups.items():
        print(f"  {name[:52]:52} {len(candidates)} candidate(s), "
              f"best {candidates[0][1]:.5f}")

    if not args.write:
        print("\ndry run only; pass --write to append oracle receipts")
        return 0

    run_id = f"exact-reverify-{int(time.time())}"
    rows = []
    for name, candidates in groups.items():
        ws = workspace.bootstrap(repo, name)
        result = {"function": name, "exact": False, "attempts": []}
        for aid, old_score, source in candidates:
            att = workspace.score(
                ws, repo, f"{name}_reverify_{aid}", source,
                conn=conn, func=name, strategy="exact-reverify", run_id=run_id,
                extra={"legacy_attempt_id": aid, "legacy_score": old_score})
            result["attempts"].append({
                "legacy_attempt_id": aid, "old_score": old_score,
                "score": att.score, "compiled": att.compiled,
                "exact": att.exact})
            state = "EXACT" if att.exact else (
                f"{att.score:.5f}" if att.compiled else "build failure")
            print(f"    {name[:48]:48} row {aid}: {state}", flush=True)
            if att.exact:
                result["exact"] = True
                break
        rows.append(result)

    exact = sum(row["exact"] for row in rows)
    print(f"\nappended receipts: {exact}/{len(rows)} functions verified exact")
    if args.out:
        args.out.write_text(json.dumps({"run_id": run_id, "rows": rows}, indent=1))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

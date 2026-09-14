"""Promote a relocated-byte exact match into the attempt ledger safely.

The ordinary object diff cannot equate a ROM-named jump table with a local
compiler section.  A relocated receipt can, but only after this command
recompiles the supplied source and proves that its object hash is the exact
candidate bound into the receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from solver import workspace
from tools import relocated_oracle


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_from_attempt(
        conn: sqlite3.Connection, attempt_id: int, function: str) -> str:
    row = conn.execute(
        "SELECT f.name, a.source_code FROM attempts a "
        "JOIN functions f ON f.addr = a.func_addr WHERE a.id = ?",
        (attempt_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"attempt {attempt_id} does not exist")
    if row[0] != function:
        raise ValueError(
            f"attempt {attempt_id} belongs to {row[0]}, not {function}")
    if not row[1]:
        raise ValueError(f"attempt {attempt_id} has no source_code")
    return str(row[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("function")
    parser.add_argument("source", nargs="?",
                        help="candidate source, or RECEIPT when --attempt-id is used")
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument(
        "--attempt-id", type=int,
        help="recompile source_code from this immutable attempt receipt")
    parser.add_argument("--repo", type=Path,
                        default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "decomp/kb-sbk1.sqlite")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db, timeout=60)
    if args.attempt_id is not None:
        if args.receipt is not None:
            parser.error("with --attempt-id, pass only FUNCTION RECEIPT")
        if args.source is None:
            parser.error("receipt is required")
        receipt_path = Path(args.source)
        try:
            source = source_from_attempt(conn, args.attempt_id, args.function)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        if args.source is None or args.receipt is None:
            parser.error("FUNCTION SOURCE RECEIPT are required")
        receipt_path = args.receipt
        source = Path(args.source).read_text(encoding="utf-8")

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    relocated_oracle.validate_exact_receipt(receipt)

    ws = workspace.bootstrap(args.repo, args.function)
    ordinary = workspace.score(
        ws, args.repo, args.function, source,
        conn=conn, func=args.function,
        strategy="relocated-promotion-recompile",
        extra={"receipt": str(receipt_path),
               "source_attempt_id": args.attempt_id},
    )
    if not ordinary.compiled:
        raise SystemExit("promotion recompile failed")

    candidate = ws / f"{args.function}.o"
    target = ws / "target.o"
    expected_candidate = receipt.get("candidate", {}).get("sha256")
    expected_target = receipt.get("target", {}).get("sha256")
    if _sha256(candidate) != expected_candidate:
        raise SystemExit("recompiled candidate object does not match receipt")
    if _sha256(target) != expected_target:
        raise SystemExit("current target object does not match receipt")

    promoted = workspace.Attempt(
        compiled=True,
        score=100.0,
        exact=True,
        diff=json.dumps(receipt, sort_keys=True),
        compiler_stderr="",
        raw_output=("Relocated text and every candidate data section are "
                    "byte-exact; candidate and target object hashes rebound."),
    )
    workspace.log_attempt(
        conn, args.function, source, promoted,
        strategy="relocated-oracle-exact",
        extra={
            "receipt": str(receipt_path),
            "receipt_sha256": _sha256(receipt_path),
            "ordinary_score": ordinary.score,
            "source_attempt_id": args.attempt_id,
        },
    )
    print(f"Verified relocated exact match: {args.function}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

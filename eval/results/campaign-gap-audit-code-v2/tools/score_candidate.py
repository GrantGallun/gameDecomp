"""Compile one source file against a function oracle and log the receipt.

Run from the gameDecomp checkout inside the target's Linux environment.  This
is the small, auditable path for a hand-derived candidate; it deliberately
uses the same workspace oracle and attempt logger as the autonomous passes.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from solver import workspace


def rewrite_source(
    source: str,
    *,
    replacements: list[tuple[str, str]] | None = None,
    replace_all: list[tuple[str, str]] | None = None,
    header_masks: list[tuple[str, str]] | None = None,
) -> str:
    """Apply explicit, receipt-friendly source transformations."""
    for old, new in replacements or []:
        count = source.count(old)
        if count != 1:
            raise ValueError(
                f"--replace OLD must occur exactly once; found {count}: {old!r}")
        source = source.replace(old, new, 1)
    for old, new in replace_all or []:
        count = source.count(old)
        if not old or count == 0:
            raise ValueError(
                "--replace-all OLD must be nonempty and occur at least once: "
                f"{old!r}")
        source = source.replace(old, new)
    for name, alias in header_masks or []:
        if not name.isidentifier() or not alias.isidentifier():
            raise ValueError("--mask-header-symbol values must be C identifiers")
        lines = source.splitlines(keepends=True)
        include_indices = [
            i for i, line in enumerate(lines)
            if line.lstrip().startswith("#include ")
        ]
        if not include_indices:
            raise ValueError(
                "--mask-header-symbol requires at least one #include")
        lines.insert(include_indices[-1] + 1, f"#undef {name}\n")
        source = f"#define {name} {alias}\n" + "".join(lines)
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("function")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument(
        "--attempt-id", type=int,
        help="replay source_code from this immutable SQLite attempt receipt "
             "instead of reading a source file",
    )
    parser.add_argument(
        "--best-attempt", action="store_true",
        help="replay the highest-scoring compiled receipt for FUNCTION",
    )
    parser.add_argument("--repo", type=Path,
                        default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--strategy", default="manual-oracle")
    parser.add_argument("--model", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument(
        "--replace", action="append", nargs=2, default=[],
        metavar=("OLD", "NEW"),
        help="replace one uniquely occurring source fragment before scoring "
             "(repeatable)",
    )
    parser.add_argument(
        "--replace-all", action="append", nargs=2, default=[],
        metavar=("OLD", "NEW"),
        help="replace every occurrence of a nonempty source fragment "
             "(repeatable; intended for stable-address symbol aliases)",
    )
    parser.add_argument(
        "--mask-header-symbol", action="append", nargs=2, default=[],
        metavar=("NAME", "HEADER_ALIAS"),
        help="temporarily macro-alias NAME while processing includes, then "
             "undefine it before explicit historical declarations",
    )
    args = parser.parse_args()

    selectors = sum((args.source is not None, args.attempt_id is not None,
                     args.best_attempt))
    if selectors != 1:
        parser.error("provide exactly one SOURCE, --attempt-id, or --best-attempt")

    conn = sqlite3.connect(args.db, timeout=60)
    if args.best_attempt:
        row = conn.execute(
            """
            select a.id, a.source_code
              from attempts a join functions f on f.addr = a.func_addr
             where f.name = ? and a.compiled = 1
             order by a.score desc, a.id desc limit 1
            """,
            (args.function,),
        ).fetchone()
        if row is None:
            parser.error(f"no compiled attempts exist for {args.function}")
        print(f"Replaying best attempt {row[0]}")
        source = row[1]
    elif args.attempt_id is not None:
        row = conn.execute(
            "select source_code from attempts where id = ?", (args.attempt_id,)
        ).fetchone()
        if row is None:
            parser.error(f"attempt {args.attempt_id} does not exist")
        source = row[0]
    else:
        source = args.source.read_text(encoding="utf-8")
    try:
        source = rewrite_source(
            source,
            replacements=args.replace,
            replace_all=args.replace_all,
            header_masks=args.mask_header_symbol,
        )
    except ValueError as exc:
        parser.error(str(exc))
    attempt = workspace.score(
        workspace.bootstrap(args.repo, args.function),
        args.repo,
        args.function,
        source,
        conn=conn,
        func=args.function,
        strategy=args.strategy,
        model=args.model,
        prompt=args.prompt,
    )
    print(attempt.raw_output)
    if not attempt.compiled:
        return 1
    return 0 if attempt.exact else 2


if __name__ == "__main__":
    raise SystemExit(main())

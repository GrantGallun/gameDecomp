"""Deliberately recover and oracle-score one function from target git source.

This is not a solver or benchmark path.  It reads source from the target
repository itself and therefore requires an explicit acknowledgement flag.
Use it only for user-authorized historical recovery; never use its source in a
held-out/model prompt.  The current target compiler and byte oracle still make
the promotion decision, and the attempt strategy records the provenance.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

from solver import workspace
from tools import n64_corpus


DO_BLOCK_RE = re.compile(r"\bdo\s*\{")


def rewrite_do_while(code: str, style: str = "for-break") -> str:
    """Express bottom-tested loops without the build-forbidden ``do`` token.

    ``style="while"`` is only semantics-preserving when surrounding control
    flow already proves the condition true on entry; callers opt into that
    stronger precondition explicitly.
    """
    while True:
        masked = n64_corpus._mask_noncode(code)
        matches = list(DO_BLOCK_RE.finditer(masked))
        if not matches:
            return code
        match = matches[-1]
        opening = masked.find("{", match.start())
        closing = n64_corpus._matching_right(masked, opening, "{", "}")
        if closing is None:
            raise ValueError("unbalanced do-while body")
        cursor = closing + 1
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if not masked.startswith("while", cursor):
            raise ValueError("do block is not followed by while condition")
        cursor += len("while")
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        if cursor >= len(masked) or masked[cursor] != "(":
            raise ValueError("do-while condition has no opening parenthesis")
        condition_end = n64_corpus._matching_right(masked, cursor, "(", ")")
        if condition_end is None:
            raise ValueError("unbalanced do-while condition")
        end = condition_end + 1
        while end < len(masked) and masked[end].isspace():
            end += 1
        if end >= len(masked) or masked[end] != ";":
            raise ValueError("do-while condition has no trailing semicolon")

        body = code[opening + 1:closing]
        condition = code[cursor + 1:condition_end]
        # IDO's register allocator can react to a syntactic loop even when its
        # body and condition fold away.  An empty do {} while (0) has no
        # observable behavior, so erase that exact shape instead of replacing
        # it with another loop-shaped construct.
        if not n64_corpus._mask_noncode(body).strip() and condition.strip() == "0":
            code = code[:match.start()] + "{}" + code[end + 1:]
            continue
        # A continue in this body targets the condition in a do-while but the
        # loop head in a for-loop.  Refuse instead of silently changing code.
        from solver.rewrites import _has_own_level_continue
        if _has_own_level_continue(body):
            raise ValueError("cannot safely rewrite do-while with loop-level continue")
        line_start = code.rfind("\n", 0, match.start()) + 1
        indent = re.match(r"[ \t]*", code[line_start:match.start()]).group(0)
        if style == "while":
            replacement = f"while ({condition}) {{{body}\n{indent}}}"
        else:
            replacement = (f"for (;;) {{{body}\n{indent}    "
                           f"if (!({condition})) break;\n{indent}}}")
        code = code[:match.start()] + replacement + code[end + 1:]


def parse_line_range(value: str) -> tuple[int, int]:
    try:
        start, end = (int(part) for part in value.split(":", 1))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"expected START:END line range, got {value!r}") from exc
    if start < 1 or end < start:
        raise ValueError(f"invalid line range {value!r}")
    return start, end


def candidate_from_file(path: Path, symbol: str,
                        extra_ranges: list[tuple[int, int]] | None = None,
                        rewrite_do: bool | str = False,
                        extra_declarations: list[str] | None = None,
                        excluded_includes: list[str] | None = None) -> str:
    source = path.read_text(encoding="utf-8", errors="replace")
    matches = [record for record in n64_corpus.extract_functions(source)
               if record["name"] == symbol]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {symbol} definition in {path}, found {len(matches)}")
    excluded_includes = excluded_includes or []
    includes = [
        line for line in source.splitlines()
        if line.lstrip().startswith("#include ")
        and not any(token in line for token in excluded_includes)
    ]
    source_lines = source.splitlines()
    extras = []
    for start, end in extra_ranges or []:
        if end > len(source_lines):
            raise ValueError(
                f"line range {start}:{end} exceeds {path} ({len(source_lines)} lines)")
        extras.append("\n".join(source_lines[start - 1:end]))
    parts = ["\n".join(includes), *extras,
             *(extra_declarations or []), str(matches[0]["definition"])]
    candidate = "\n\n".join(part for part in parts if part) + "\n"
    if not rewrite_do:
        return candidate
    rewrite_style = "for-break" if rewrite_do is True else rewrite_do
    return rewrite_do_while(candidate, rewrite_style)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("function", help="semantic target function name")
    parser.add_argument("source", type=Path)
    parser.add_argument("--source-symbol", default="")
    parser.add_argument(
        "--extra-lines", action="append", default=[], metavar="START:END",
        help="copy an explicit file-local declaration range (repeatable)")
    rewrite_group = parser.add_mutually_exclusive_group()
    rewrite_group.add_argument(
        "--rewrite-do-while", action="store_true",
        help="use the sanctioned for(;;)+break bottom-test spelling")
    rewrite_group.add_argument(
        "--rewrite-do-while-as-while", action="store_true",
        help="use while(condition); only valid when entry is independently guarded")
    parser.add_argument(
        "--extra-declaration", action="append", default=[],
        help="append one explicit standalone declaration (repeatable)")
    parser.add_argument(
        "--exclude-include", action="append", default=[], metavar="TOKEN",
        help="omit source include lines containing TOKEN (repeatable)")
    parser.add_argument("--repo", type=Path,
                        default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--allow-target-source", action="store_true")
    args = parser.parse_args()
    if not args.allow_target_source:
        parser.error("target source is ground truth; pass --allow-target-source "
                     "only for explicit recovery, never evaluation")

    symbol = args.source_symbol or args.function
    try:
        ranges = [parse_line_range(value) for value in args.extra_lines]
        rewrite_style = (
            "while" if args.rewrite_do_while_as_while
            else args.rewrite_do_while
        )
        code = candidate_from_file(
            args.source, symbol, ranges, rewrite_do=rewrite_style,
            extra_declarations=args.extra_declaration,
            excluded_includes=args.exclude_include)
    except ValueError as exc:
        parser.error(str(exc))
    conn = sqlite3.connect(args.db, timeout=60)
    attempt = workspace.score(
        workspace.bootstrap(args.repo, args.function), args.repo,
        args.function, code, conn=conn, func=args.function,
        strategy="authorized-target-history-recovery",
        extra={"source_path": str(args.source), "source_symbol": symbol,
               "rewrite_do_while": rewrite_style,
               "extra_declarations": args.extra_declaration,
               "excluded_includes": args.exclude_include},
    )
    print(attempt.raw_output)
    if not attempt.compiled:
        return 1
    return 0 if attempt.exact else 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Build and query a source-blind public N64 function provenance index.

The index stores names, locations, normalized hashes, constants, calls, and a
small bottom-k token-shingle sketch. It deliberately stores no source bodies.
External source is a discovery lead only: promotion into solver behavior still
requires compilation and the target's byte-exact oracle.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
from typing import Iterable, Iterator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "corpus" / "n64_sources.json"
DEFAULT_INDEX = ROOT / ".tools" / "n64-corpus.sqlite"
SCHEMA_VERSION = 1
SKETCH_SIZE = 96
SHINGLE_SIZE = 5

IDENT_RE = re.compile(r"[A-Za-z_]\w*")
TOKEN_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|"
    r"\d+[eE][+-]?\d+|\d+)(?:[uUlLfF]+)?|"
    r"[A-Za-z_]\w*|(?:>>=|<<=|->|\+\+|--|&&|\|\||==|!=|<=|>=|<<|>>|"
    r"\+=|-=|\*=|/=|%=|&=|\|=|\^=)|[^\s]")
NUMBER_RE = re.compile(
    r"^(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|"
    r"\d+[eE][+-]?\d+|\d+)(?:[uUlLfF]+)?$")

CONTROL = {"if", "for", "while", "switch", "return", "sizeof"}
KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default",
    "do", "double", "else", "enum", "extern", "float", "for", "goto",
    "if", "inline", "int", "long", "register", "restrict", "return",
    "short", "signed", "sizeof", "static", "struct", "switch", "typedef",
    "union", "unsigned", "void", "volatile", "while", "_Bool",
}


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported N64 corpus manifest schema")
    policy = payload.get("policy", {})
    if policy.get("direct_external_source_in_prompt") is not False:
        raise ValueError("corpus manifest must forbid direct external prompt source")
    repositories = payload.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        raise ValueError("corpus manifest has no repositories")
    ids = [repo.get("id") for repo in repositories]
    if any(not isinstance(repo_id, str) or not repo_id for repo_id in ids):
        raise ValueError("corpus repository is missing an id")
    if len(ids) != len(set(ids)):
        raise ValueError("corpus repository ids are not unique")
    return payload


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ""


def _mask_noncode(source: str) -> str:
    """Replace comments and literals with spaces while preserving newlines."""
    out = list(source)
    i = 0
    state = "code"
    quote = ""
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if ch == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if ch in "\"'":
                quote = ch
                out[i] = " "
                i += 1
                state = "literal"
                continue
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "code"
                continue
            if ch != "\n":
                out[i] = " "
        else:
            if ch == "\\" and i + 1 < len(source):
                out[i] = " "
                if source[i + 1] != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if ch == quote:
                state = "code"
            if ch != "\n":
                out[i] = " "
        i += 1
    return "".join(out)


def _matching_left(text: str, close: int, left: str, right: str) -> int | None:
    depth = 0
    for i in range(close, -1, -1):
        if text[i] == right:
            depth += 1
        elif text[i] == left:
            depth -= 1
            if depth == 0:
                return i
    return None


def _matching_right(text: str, opening: int, left: str, right: str) -> int | None:
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == left:
            depth += 1
        elif text[i] == right:
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_functions(source: str) -> Iterator[dict[str, object]]:
    """Yield brace-balanced C function records without needing preprocessing."""
    masked = _mask_noncode(source)
    newline_offsets = [index for index, ch in enumerate(source) if ch == "\n"]
    boundary = -1
    opening = 0
    while opening < len(masked):
        ch = masked[opening]
        if ch == ";":
            boundary = opening
            opening += 1
            continue
        if ch != "{":
            opening += 1
            continue
        cursor = opening - 1
        while cursor >= 0 and masked[cursor].isspace():
            cursor -= 1
        closing = _matching_right(masked, opening, "{", "}")
        if closing is None:
            break
        if cursor < 0 or masked[cursor] != ")":
            # A top-level struct/array initializer cannot contain a C function.
            boundary = closing
            opening = closing + 1
            continue
        left_paren = _matching_left(masked, cursor, "(", ")")
        if left_paren is None:
            boundary = closing
            opening = closing + 1
            continue
        name_end = left_paren
        while name_end > 0 and masked[name_end - 1].isspace():
            name_end -= 1
        name_start = name_end
        while name_start > 0 and (masked[name_start - 1].isalnum()
                                  or masked[name_start - 1] == "_"):
            name_start -= 1
        name = masked[name_start:name_end]
        if not IDENT_RE.fullmatch(name):
            boundary = closing
            opening = closing + 1
            continue
        if name in CONTROL:
            boundary = closing
            opening = closing + 1
            continue
        header = masked[boundary + 1:opening]
        if "=" in header or header.lstrip().startswith("#"):
            boundary = closing
            opening = closing + 1
            continue
        if not re.search(r"\b(?:void|char|short|int|long|float|double|struct|"
                         r"union|enum|[A-Za-z_]\w*)\b", header):
            boundary = closing
            opening = closing + 1
            continue
        body = source[opening:closing + 1]
        yield {
            "name": name,
            "line": bisect.bisect_left(newline_offsets, name_start) + 1,
            "body": body,
            # Useful to deliberate source-recovery tools.  The public corpus
            # index still stores only hashes/sketches and never persists this.
            "definition": source[boundary + 1:closing + 1].strip(),
        }
        boundary = closing
        opening = closing + 1


def _canonical_number(token: str) -> str:
    raw = re.sub(r"[uUlLfF]+$", "", token)
    try:
        if raw.lower().startswith("0x"):
            return f"N{int(raw, 16)}"
        if any(ch in raw for ch in ".eE"):
            return f"F{float(raw):.12g}"
        return f"N{int(raw, 10)}"
    except ValueError:
        return "NUM"


def normalized_tokens(body: str) -> tuple[list[str], list[str], list[str]]:
    """Return normalized tokens plus distinctive constants and call names."""
    masked = _mask_noncode(body)
    raw_tokens = TOKEN_RE.findall(masked)
    tokens: list[str] = []
    constants: list[str] = []
    calls: list[str] = []
    for index, token in enumerate(raw_tokens):
        if NUMBER_RE.match(token):
            canonical = _canonical_number(token)
            tokens.append(canonical)
            constants.append(canonical)
        elif IDENT_RE.fullmatch(token):
            if token in KEYWORDS:
                tokens.append(token)
            else:
                next_token = raw_tokens[index + 1] if index + 1 < len(raw_tokens) else ""
                prev_token = raw_tokens[index - 1] if index else ""
                if next_token == "(":
                    tokens.append("CALL")
                    calls.append(token)
                elif prev_token in {".", "->"}:
                    tokens.append("FIELD")
                else:
                    tokens.append("ID")
        else:
            tokens.append(token)
    return tokens, sorted(set(constants)), sorted(set(calls))


def sketch(tokens: list[str], size: int = SKETCH_SIZE,
           width: int = SHINGLE_SIZE) -> tuple[int, ...]:
    if not tokens:
        return ()
    if len(tokens) < width:
        windows: Iterable[tuple[str, ...]] = [tuple(tokens)]
    else:
        windows = (tuple(tokens[i:i + width])
                   for i in range(len(tokens) - width + 1))
    values = {
        int.from_bytes(hashlib.blake2b(
            "\x1f".join(window).encode(), digest_size=8).digest(), "big")
        for window in windows
    }
    return tuple(sorted(values)[:size])


def sketch_similarity(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    # A transparent bottom-k overlap proxy, not a semantic proof.
    return len(set(left) & set(right)) / min(len(left), len(right))


def _pack(values: tuple[int, ...]) -> bytes:
    return struct.pack(f">{len(values)}Q", *values) if values else b""


def _unpack(value: bytes) -> tuple[int, ...]:
    return struct.unpack(f">{len(value) // 8}Q", value) if value else ()


def _source_files(repo_path: Path, roots: list[str]) -> Iterator[Path]:
    for relative in roots:
        root = repo_path / relative
        if not root.is_dir():
            continue
        for path in root.rglob("*.c"):
            parts = set(path.relative_to(repo_path).parts)
            if parts & {"build", "assets", "generated", ".git"}:
                continue
            yield path


def build_index(manifest_path: Path, output: Path) -> dict[str, object]:
    manifest = load_manifest(manifest_path)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    if temporary.exists():
        temporary.unlink()
    conn = sqlite3.connect(temporary)
    conn.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE repositories(
            id TEXT PRIMARY KEY, url TEXT NOT NULL, head TEXT NOT NULL,
            shallow INTEGER NOT NULL, evaluation_policy TEXT NOT NULL,
            source_files INTEGER NOT NULL, function_count INTEGER NOT NULL);
        CREATE TABLE functions(
            id INTEGER PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL,
            line INTEGER NOT NULL, name TEXT NOT NULL, token_count INTEGER NOT NULL,
            normalized_sha256 TEXT NOT NULL, sketch BLOB NOT NULL,
            constants_json TEXT NOT NULL, calls_json TEXT NOT NULL);
        CREATE INDEX functions_name ON functions(name);
        CREATE INDEX functions_hash ON functions(normalized_sha256);
        CREATE INDEX functions_repo ON functions(repo_id);
    """)
    conn.execute("INSERT INTO metadata VALUES(?,?)", ("schema_version", str(SCHEMA_VERSION)))
    conn.execute("INSERT INTO metadata VALUES(?,?)", (
        "policy", json.dumps(manifest["policy"], sort_keys=True)))
    totals = {"repositories": 0, "source_files": 0, "functions": 0, "missing": []}
    try:
        for repository in manifest["repositories"]:
            repo_path = (ROOT / repository["local_path"]).resolve()
            if not (repo_path / ".git").exists():
                totals["missing"].append(repository["id"])
                continue
            head = _git(repo_path, "rev-parse", "HEAD")
            shallow = _git(repo_path, "rev-parse", "--is-shallow-repository") == "true"
            file_count = 0
            function_count = 0
            for path in _source_files(repo_path, repository["source_roots"]):
                file_count += 1
                source = path.read_text(encoding="utf-8", errors="replace")
                for function in extract_functions(source):
                    tokens, constants, calls = normalized_tokens(str(function["body"]))
                    digest = hashlib.sha256("\x1f".join(tokens).encode()).hexdigest()
                    conn.execute(
                        "INSERT INTO functions(repo_id,path,line,name,token_count,"
                        "normalized_sha256,sketch,constants_json,calls_json) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (repository["id"], str(path.relative_to(repo_path)).replace("\\", "/"),
                         function["line"], function["name"], len(tokens), digest,
                         _pack(sketch(tokens)), json.dumps(constants), json.dumps(calls)))
                    function_count += 1
            conn.execute(
                "INSERT INTO repositories VALUES(?,?,?,?,?,?,?)",
                (repository["id"], repository["url"], head, int(shallow),
                 repository["evaluation_policy"], file_count, function_count))
            totals["repositories"] += 1
            totals["source_files"] += file_count
            totals["functions"] += function_count
        conn.commit()
    finally:
        conn.close()
    if output.exists():
        output.unlink()
    temporary.replace(output)
    return {**totals, "output": str(output), "bytes": output.stat().st_size}


def query_symbol(index: Path, symbol: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT f.repo_id,f.path,f.line,f.name,f.token_count,f.normalized_sha256,"
            "r.head,r.evaluation_policy FROM functions f JOIN repositories r "
            "ON r.id=f.repo_id WHERE f.name=? ORDER BY f.repo_id,f.path,f.line",
            (symbol,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def query_body(index: Path, body: str, *, top: int = 20,
               exclude_repo: str = "") -> list[dict[str, object]]:
    tokens, _constants, _calls = normalized_tokens(body)
    wanted = sketch(tokens)
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT f.repo_id,f.path,f.line,f.name,f.token_count,f.normalized_sha256,"
            "f.sketch,r.head,r.evaluation_policy FROM functions f "
            "JOIN repositories r ON r.id=f.repo_id WHERE f.repo_id != ? AND "
            "f.token_count BETWEEN ? AND ?",
            (exclude_repo, max(1, len(tokens) // 3), max(3, len(tokens) * 3))).fetchall()
        ranked = []
        for row in rows:
            record = dict(row)
            record["similarity"] = round(
                sketch_similarity(wanted, _unpack(record.pop("sketch"))), 6)
            ranked.append(record)
        ranked.sort(key=lambda row: (-float(row["similarity"]),
                                     abs(int(row["token_count"]) - len(tokens)),
                                     str(row["repo_id"]), str(row["path"])))
        return ranked[:top]
    finally:
        conn.close()


def index_stats(index: Path) -> dict[str, object]:
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    try:
        repositories = [dict(row) for row in conn.execute(
            "SELECT * FROM repositories ORDER BY id")]
        return {
            "schema_version": int(conn.execute(
                "SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]),
            "repositories": repositories,
            "repository_count": len(repositories),
            "source_files": sum(row["source_files"] for row in repositories),
            "functions": sum(row["function_count"] for row in repositories),
            "bytes": index.stat().st_size,
        }
    finally:
        conn.close()


def _function_body(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8", errors="replace")
    matches = [function for function in extract_functions(source)
               if function["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"expected one {name} definition in {path}, found {len(matches)}")
    return str(matches[0]["body"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build.add_argument("--output", type=Path, default=DEFAULT_INDEX)
    stats = sub.add_parser("stats")
    stats.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    symbol = sub.add_parser("symbol")
    symbol.add_argument("symbol")
    symbol.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    similar = sub.add_parser("similar")
    similar.add_argument("--source-file", type=Path, required=True)
    similar.add_argument("--function", required=True)
    similar.add_argument("--exclude-repo", default="")
    similar.add_argument("--top", type=int, default=20)
    similar.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    args = parser.parse_args()

    if args.command == "build":
        result = build_index(args.manifest.resolve(), args.output.resolve())
    elif args.command == "stats":
        result = index_stats(args.index.resolve())
    elif args.command == "symbol":
        result = query_symbol(args.index.resolve(), args.symbol)
    else:
        body = _function_body(args.source_file.resolve(), args.function)
        result = query_body(args.index.resolve(), body, top=args.top,
                            exclude_repo=args.exclude_repo)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""m2c placeholder declarations that stop IDO at the first line (2026-09-14 non-compiling census).

Of 103 pending functions whose stored source does not compile, 32 fail first on an m2c
placeholder the draft never replaced:

  ? drawMenuAsciiTextDefaultScale(?, ?, ? *, ?);      /* extern */   (17: unknown `?` types)
  extern M2C_UNK gHuffmanNodes;                                       (15: M2C_UNK spelling)

IDO stops at the first syntax error, so none of the downstream recovery or byte repair ever
sees these functions. Two source-level resolutions, both proposals for the compiler:

  * drop the placeholder when an included project header already declares the name
    (the declaration is context, not a guess);
  * otherwise spell the unknown as the least committal scalar: `s32` for values and
    parameters, `void *` for `? *`, and `u8` for an extern whose every use takes its address
    (byte-unit arithmetic, which is how m2c wrote those uses).

Anything else on the line (a named struct pointer, a known return type) is kept.
"""
from __future__ import annotations

import re
from pathlib import Path

from solver import project_headers, repair_context

UNKNOWN = r"(?:\?|M2C_UNK)"
PROTOTYPE = re.compile(rf"^[ \t]*(?P<decl>[^;\n#{{}}]*?\b(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^;{{}}\n]*)\))\s*;[ \t]*(?:/\*\s*extern\s*\*/)?[ \t]*$", re.M)
EXTERN = re.compile(rf"^[ \t]*extern\s+{UNKNOWN}\s*(?P<stars>\**)\s*(?P<name>[A-Za-z_]\w*)\s*(?P<array>\[[^\]]*\])?\s*;[ \t]*(?:/\*.*?\*/)?[ \t]*$", re.M)
LOCAL = re.compile(rf"^(?P<i>[ \t]+){UNKNOWN}\s*(?P<stars>\**)\s*(?P<name>[A-Za-z_]\w*)\s*;", re.M)
INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)


def _has_unknown(text: str) -> bool:
    # Type position only: `? f(?, ? *)`, never a conditional operator (`a ? 1 : 2`).
    return (re.search(r"\bM2C_UNK\b", text) is not None
            or re.search(r"(?:^|[(,])\s*\?(?=\s*[\w*,)])", text.strip()) is not None)


def header_names(repo: Path, source: str, limit: int = 400) -> str:
    """Concatenated text of the included project headers, following nested quoted includes."""
    roots = [repo, repo / "include", repo / "src", repo / "include/PR", repo / "src/ultra/audio", repo / "src/ultra/libc"]
    pending, seen, texts = list(INCLUDE.findall(source)), set(), []
    while pending and len(seen) < limit:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = next((root / name for root in roots if (root / name).is_file()), None)
        if path is None:
            continue
        text = path.read_text(errors="replace")
        texts.append(project_headers._mask_noncode(text))
        pending.extend(INCLUDE.findall(text))
    return "\n".join(texts)


def _declared(headers: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", headers) is not None


def _typed_prototype(decl: str) -> str:
    decl = re.sub(rf"(?<![\w?]){UNKNOWN}\s*\*", "void *", decl)
    return re.sub(rf"(?<![\w?]){UNKNOWN}(?![\w?])", "s32", decl)


def _signal_prototypes(source: str):
    """File-scope prototype lines only (a body statement such as `g(a ? 1 : 2);` is not a declaration)."""
    depth, cursor, masked = 0, 0, project_headers._mask_noncode(source)
    for found in PROTOTYPE.finditer(source):
        depth += masked[cursor:found.start()].count("{") - masked[cursor:found.start()].count("}")
        cursor = found.start()
        if depth == 0:
            yield found


def propose(source: str, function: str, headers: str = "") -> tuple[list[tuple[str, str]], dict]:
    """[(label, candidate)] plus a report of each placeholder and how it was resolved."""
    report = {"placeholders": [], "declines": []}
    try:
        match, end = repair_context.definition(source, function)
    except ValueError as exc:
        report["declines"].append(str(exc))
        return [], report
    body = source[match.end():end]
    outside = [(0, source[:match.start()])]
    edits_drop, edits_typed = [], []          # (start, stop, replacement) absolute
    for base, region in outside:
        for found in PROTOTYPE.finditer(region):
            if found.group("name") == function or not _has_unknown(found.group("decl")):
                continue
            start, stop = base + found.start(), base + found.end()
            typed = found.group(0).replace(found.group("decl"), _typed_prototype(found.group("decl")))
            typed = re.sub(r"\s*/\*\s*extern\s*\*/", "", typed)
            declared = _declared(headers, found.group("name"))
            report["placeholders"].append({"name": found.group("name"), "kind": "prototype", "header_declared": declared})
            edits_typed.append((start, stop, typed))
            edits_drop.append((start, stop, "" if declared else typed))
        for found in EXTERN.finditer(region):
            name = found.group("name")
            start, stop = base + found.start(), base + found.end()
            uses = re.findall(rf"(&\s*)?\b{re.escape(name)}\b", body)
            address_only = bool(uses) and all(prefix for prefix in uses)
            spelled = "u8" if address_only and not found.group("stars") else "s32"
            typed = f"extern {spelled} {found.group('stars')}{name}{found.group('array') or ''};"
            declared = _declared(headers, name)
            report["placeholders"].append({"name": name, "kind": "extern", "header_declared": declared,
                                           "address_only": address_only})
            edits_typed.append((start, stop, typed))
            edits_drop.append((start, stop, "" if declared else typed))
    for found in LOCAL.finditer(body):
        start, stop = match.end() + found.start(), match.end() + found.end()
        typed = f"{found.group('i')}{'void' if found.group('stars') else 's32'} {found.group('stars')}{found.group('name')};"
        report["placeholders"].append({"name": found.group("name"), "kind": "local"})
        edits_typed.append((start, stop, typed))
        edits_drop.append((start, stop, typed))

    def apply(edits):
        text = source
        for start, stop, replacement in sorted(edits, reverse=True):
            if replacement == "" and text[stop:stop + 1] == "\n":
                stop += 1
            text = text[:start] + replacement + text[stop:]
        return text

    rows = []
    for label, edits in (("placeholders:typed", edits_typed), ("placeholders:header-first", edits_drop)):
        candidate = apply(edits) if edits else source
        if candidate != source and candidate not in {c for _l, c in rows}:
            rows.append((label, candidate))
    return rows, report


def signals(source: str) -> bool:
    """Whether any placeholder declaration shape is present (scheduling)."""
    return (EXTERN.search(source) is not None or LOCAL.search(source) is not None
            or any(_has_unknown(m.group("decl")) for m in _signal_prototypes(source)))

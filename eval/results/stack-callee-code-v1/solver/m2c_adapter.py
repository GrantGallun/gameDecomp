"""Deterministic repairs that preserve an m2c draft's recovered logic.

m2c often emits an unknown declaration for an absolute linker symbol:

    extern ? D_3FFFF;
    value = (s32) (&D_3FFFF + value) >> 18;

The linker's undefined-symbol file already states `D_3FFFF = 0x3FFFF`.
Replacing the address expression with that literal is mechanical and keeps the
control/data flow m2c recovered from the target binary intact.  Header variants
then provide the project's reconstructed prototypes and structure layouts.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re

from solver import (buildtypes, globaldecl, memberaccess, project_headers,
                    unknowns)


_SYMBOL_DEFINITION = re.compile(
    r"^\s*([A-Za-z_][\w.$]*)\s*=\s*(0x[0-9A-Fa-f]+|[0-9]+)\s*;",
    re.M)
_UNKNOWN_EXTERN = re.compile(
    r"^[ \t]*extern\s+\?\s+([A-Za-z_][\w.$]*)\s*;[ \t]*(?:\n|$)",
    re.M)


@dataclass(frozen=True)
class Adaptation:
    source: str
    label: str
    resolved_absolute_symbols: tuple[tuple[str, int], ...] = ()
    declared_globals: tuple[str, ...] = ()
    context_receipts: tuple[dict, ...] = ()


def absolute_symbols(repo: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for name in ("undefined_syms_auto.txt", "undefined_syms.txt"):
        path = repo / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _SYMBOL_DEFINITION.finditer(text):
            values[match.group(1)] = int(match.group(2), 0)
    return values


def resolve_absolute_unknowns(repo: Path, draft: str) -> Adaptation:
    """Resolve only unknown externs backed by an explicit linker value."""
    symbols = absolute_symbols(repo)
    resolved: list[tuple[str, int]] = []
    source = draft
    for match in list(_UNKNOWN_EXTERN.finditer(draft)):
        name = match.group(1)
        if name not in symbols:
            continue
        literal = f"0x{symbols[name]:X}"
        address_use = re.compile(rf"&\s*{re.escape(name)}\b")
        if not address_use.search(source):
            # An unresolved value/lvalue use has type semantics we cannot
            # infer from the linker address alone.
            continue
        source = address_use.sub(literal, source)
        source = re.sub(
            rf"^[ \t]*extern\s+\?\s+{re.escape(name)}\s*;[ \t]*(?:\n|$)",
            "", source, flags=re.M)
        resolved.append((name, symbols[name]))
    return Adaptation(
        source, "m2c:absolute-symbols",
        tuple(sorted(resolved)))


def variants(repo: Path, function: str, asm: str,
             draft: str) -> tuple[Adaptation, ...]:
    """Return logic-preserving m2c adaptations, most contextual last."""
    base = resolve_absolute_unknowns(repo, draft)
    # m2c represents byte-stream reads as ``u8_ptr->unkN``.  Project headers
    # correctly keep the parameter as ``u8 *``, so merely adding the header
    # still leaves an invalid selector and prevents a known struct definition
    # for another parameter from helping.  The member name already encodes the
    # byte offset; normalize it before constructing every header variant.
    normalized, member_plans = memberaccess.rewrite(base.source, function)
    if member_plans:
        base = Adaptation(
            normalized,
            f"{base.label}+byte-member-index:{len(member_plans)}",
            base.resolved_absolute_symbols,
            base.declared_globals,
        )
    rows = [base]
    seen = {base.source}
    for label, source in project_headers.preflight_variants(
            repo, function, asm, base.source):
        if source in seen:
            continue
        seen.add(source)
        rows.append(Adaptation(
            source, f"{base.label}+{label}",
            base.resolved_absolute_symbols))
    target = repo / "nonmatchings" / function / "target.s"
    if (target.is_file() and (repo / ".venv/bin/m2c").is_file()
            and (repo / "tools/m2ctx.py").is_file()):
        from solver import m2c_context
        # Before source-level guesswork, let the decompiler use the ABI and
        # layout context that our isolated compiler will also use. Keep raw
        # assembly-only drafts and all context failures as separate evidence.
        contextual, receipts = m2c_context.seed_variants(repo, function, target, asm, draft)
        rows[0] = replace(rows[0], context_receipts=tuple(receipts))
        for label, source in contextual:
            if source not in seen:
                seen.add(source)
                rows.append(Adaptation(source, label, context_receipts=tuple(receipts)))
    return tuple(rows)


def evidence_global_variants(conn, repo: Path, function: str,
                             rows: tuple[Adaptation, ...]
                             ) -> tuple[Adaptation, ...]:
    """Add variants declaring only globals whose width the binary supports.

    ``symbol_addrs.txt`` establishes identity/address while the evidence DB
    establishes access width and signedness.  This is compile repair, not a
    semantic guess, and deliberately does not invent function prototypes.
    """
    known_types = buildtypes.type_names(repo)
    symbols = unknowns.symbol_table(repo)
    out = list(rows)
    seen = {row.source for row in rows}
    for row in rows:
        source, plans = globaldecl.declare(
            conn, row.source, function, known_types, symbols)
        if not plans or source in seen:
            continue
        seen.add(source)
        out.append(Adaptation(
            source=source,
            label=f"{row.label}+binary_globals:{len(plans)}",
            resolved_absolute_symbols=row.resolved_absolute_symbols,
            declared_globals=tuple(plan["name"] for plan in plans),
            context_receipts=row.context_receipts,
        ))
    return tuple(out)


def prioritized_variants(conn, repo: Path, function: str, asm: str, draft: str,
                         limit: int = 4) -> tuple[Adaptation, ...]:
    """Raw baseline plus the most complete contextual candidates, bounded."""
    rows = variants(repo, function, asm, draft)
    contextual = [row for row in reversed(rows) if "project-header-" in row.label]
    ordered = [rows[0], *contextual, *rows[1:]]
    unique, seen = [], set()
    for row in ordered:
        if row.source not in seen:
            unique.append(row)
            seen.add(row.source)
    return evidence_global_variants(conn, repo, function, tuple(unique))[:limit]

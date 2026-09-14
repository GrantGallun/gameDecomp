"""Bounded declaration feedback for m2c, never reference function bodies.

Headers resolve ABI arities and array indexing inside m2c itself. A second pass
can feed back explicit typed pointer assignments and binary-extracted constant
tables. These declarations are hypotheses; compilation and the oracle judge
their result. Nothing here defines storage or writes the evidence database.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

from solver import c89, m2c_input, project_headers


def indexed_externs(source: str) -> list[dict]:
    masked = c89._mask(source)
    unknown = set(re.findall(r"(?m)^extern\s+\?\s+(\w+)\s*;", masked))
    pointers: dict[str, set[str]] = {}
    scalars = set(re.findall(r"\b(?:s32|u32|s16|u16|s8|u8|int|short|char)\s+(\w+)\b", masked))
    for match in re.finditer(r"(?m)^\s*(\w+)\s*\*\s*(\w+)\s*;", masked):
        if match.group(1) not in {"void", "const", "volatile"}:
            pointers.setdefault(match.group(2), set()).add(match.group(1))
    inferred: dict[str, list[tuple[str, str]]] = {}
    for match in re.finditer(r"(?m)^\s*(\w+)\s*=\s*([^;{}]+);", masked):
        types = pointers.get(match.group(1), set())
        if len(types) != 1:
            continue
        expression = match.group(2).strip()
        # Only an address plus side-effect-free integer offset. Do not interpret
        # a bare pointer variable, function result, multiple bases or casts.
        address = re.fullmatch(r"([\w\s()*+\-]+)\+\s*&\s*(\w+)", expression)
        if not address or address.group(2) not in unknown:
            continue
        if re.search(r"\w\s*\(|\+\+|--", address.group(1)):
            continue
        if not set(re.findall(r"\b[A-Za-z_]\w*\b", address.group(1))) <= scalars:
            continue
        name = address.group(2)
        inferred.setdefault(name, []).append((next(iter(types)), match.group().strip()))
    plans = []
    for name, uses in sorted(inferred.items()):
        types = {ctype for ctype, _ in uses}
        if len(types) != 1:
            continue
        ctype = next(iter(types))
        plans.append({"symbol": name, "declaration": f"extern {ctype} {name}[];",
                      "authority": "typed-pointer assignment hypothesis; extent unknown",
                      "source_constraints": [use for _, use in uses]})
    return plans


def rodata_externs(repo: Path, source: str) -> list[dict]:
    """Homogeneous literal float tables in extracted assembly, not C sources."""
    unknown = set(re.findall(r"(?m)^extern\s+\?\s+(\w+)\s*;", c89._mask(source)))
    plans = []
    for name in sorted(unknown):
        matches = list((repo / "asm").rglob(name + ".s"))
        if len(matches) != 1:
            continue
        path = matches[0]
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if not re.search(r"\.section\s+\.(?:late_)?rodata\b", text):
            continue
        block = re.search(rf"(?ms)^\s*dlabel\s+{re.escape(name)}\s*$"
                          rf"(.*?)^\s*enddlabel\s+{re.escape(name)}\s*$", text)
        if not block:
            continue
        body = re.sub(r"/\*.*?\*/|#[^\n]*", "", block.group(1), flags=re.S)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        values = [re.fullmatch(r"\.(double|float)\s+[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", line)
                  for line in lines]
        if not values or any(value is None for value in values):
            continue
        types = {value.group(1) for value in values}
        if len(types) != 1:
            continue
        ctype = next(iter(types))
        plans.append({"symbol": name, "declaration": f"extern {ctype} {name}[{len(values)}];",
                      "authority": "homogeneous binary-extracted rodata directives",
                      "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                      "element_bytes": 8 if ctype == "double" else 4,
                      "observed_elements": len(values)})
    return plans


def lower_bitcasts(source: str, function: str) -> tuple[str, list[dict]]:
    """IDO-compatible union reinterpretation at the original evaluation point.

    Only explicit 32-bit scalar identifiers with a known source type. No
    numerical casts, helper calls, aliasing pointer tricks or inline assembly.
    Unsupported expressions remain visible for a later repair stage.
    """
    masked = c89._mask(source)
    definition = re.search(rf"\b{re.escape(function)}\s*\([^;{{}}]*\)\s*\{{", masked)
    if not definition:
        return source, []
    known: dict[str, set[str]] = {}
    for match in re.finditer(r"\b(f32|float|s32|u32|int|unsigned int)\s+(\w+)\b", masked):
        known.setdefault(match.group(2), set()).add(match.group(1))
    depth, closing = 1, definition.end()
    while closing < len(masked) and depth:
        depth += (masked[closing] == "{") - (masked[closing] == "}")
        closing += 1
    if depth:
        return source, []
    edits, declarations, plans, used = [], [], [], set()
    for match in re.finditer(r"\(bitwise\s+(s32|u32|f32)\)\s*(\w+)\b", masked):
        target_type, operand = match.groups()
        types = known.get(operand, set())
        if len(types) != 1 or not definition.end() <= match.start() < closing:
            continue
        tail = masked[match.end():].lstrip()
        if tail.startswith((".", "->", "[", "(", "++", "--")):
            continue
        source_type = next(iter(types))
        if (source_type in {"float", "f32"}) == (target_type == "f32"):
            continue
        index = len(plans)
        local = f"m2c_bits_{index}"
        while re.search(rf"\b{local}\b", masked) or local in used:
            index += 1
            local = f"m2c_bits_{index}"
        used.add(local)
        declarations.append(f"    union {{ {source_type} from; {target_type} to; }} {local};\n")
        edits.append((match.start(), match.end(), f"({local}.from = {operand}, {local}.to)"))
        plans.append({"source_type": source_type, "target_type": target_type,
                      "operand": operand, "local": local,
                      "authority": "m2c explicit bitwise cast; IDO union representation"})
    for start, end, replacement in reversed(edits):
        source = source[:start] + replacement + source[end:]
    if plans:
        source = source[:definition.end()] + "\n" + "".join(declarations) + source[definition.end():]
    return source, plans


def _seed_variants(repo: Path, function: str, target: Path, assembly: str, seed: str):
    """At most two context passes, with decline/failure receipts retained."""
    result, metadata = m2c_input.header_draft(repo, function, target, assembly, seed)
    receipts = [{**metadata, "returncode": result.returncode,
                 "diagnostic": (result.stdout + result.stderr)[-2000:] if result.returncode else ""}]
    if result.returncode:
        return [], receipts
    headers = metadata["headers"]
    prefix = "".join(f'#include "{include}"\n' for include in headers)
    source = prefix + result.stdout
    plans = indexed_externs(source) + rodata_externs(repo, source)
    by_symbol = {}
    conflicts = set()
    for plan in plans:
        symbol = plan["symbol"]
        if symbol in by_symbol and by_symbol[symbol]["declaration"] != plan["declaration"]:
            conflicts.add(symbol)
        by_symbol[symbol] = plan
    plans = [plan for symbol, plan in by_symbol.items() if symbol not in conflicts]
    receipts[0]["declined_conflicting_symbols"] = sorted(conflicts)
    rows = [("project-header-m2c-context", source)]
    if plans:
        declarations = "\n".join(plan["declaration"] for plan in plans) + "\n"
        second, meta = m2c_input.draft(repo, target, context_headers=tuple(headers),
                                     extra_declarations=declarations)
        receipts.append({**meta, "returncode": second.returncode, "declaration_plans": plans,
                         "diagnostic": (second.stdout + second.stderr)[-2000:] if second.returncode else ""})
        if second.returncode == 0:
            rows.append(("project-header-m2c-declaration-feedback", prefix + declarations + second.stdout))
    for label, code in list(rows):
        lowered, casts = lower_bitcasts(code, function)
        if casts:
            rows.append((label + "+bitcast-union", lowered))
            receipts.append({"stage": "bitcast-lowering", "plans": casts})
    return rows, receipts


def seed_variants(repo: Path, function: str, target: Path, assembly: str, seed: str):
    try:
        return _seed_variants(repo, function, target, assembly, seed)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Context is an optional candidate branch, not a reason to lose the
        # raw draft or stop siblings. Preserve operational failure explicitly.
        return [], [{"stage": "context-seed", "status": "failed",
                     "diagnostic": f"{type(exc).__name__}: {exc}"}]

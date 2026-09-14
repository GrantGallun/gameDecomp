"""Zero-token project-header context for isolated function workspaces.

The bootstrap draft deliberately starts with only ``common.h``.  That is not
always the translation unit that declared the function: a perfectly good m2c
body can fail merely because its parameter struct lives in a narrower project
header.  Search reconstructed headers (never the target C file), add the
header that declares the function, and let the compiler/oracle judge it.

For an assembly-certified empty return, the same declaration can safely be
turned into an empty definition.  This recovers cases where m2c emits no body
at all without asking a model to rediscover ``void f(...) {}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class HeaderDeclaration:
    include: str
    prototype: str


def _mask_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", lambda match: " " * len(match.group()),
                  text, flags=re.S)


def _mask_noncode(text: str) -> str:
    return re.sub(
        r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
        lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)


def declarations(repo: Path, function: str,
                 max_results: int = 12, *, all_variants: bool = False) -> list[HeaderDeclaration]:
    """Find ordinary function prototypes in ``include/**/*.h``.

    The result is compiler context, not semantic truth: reconstructed project
    headers are already inputs to the real build, and exactness is still
    decided exclusively by object comparison.
    """
    include_root = Path(repo).expanduser() / "include"
    if not include_root.is_dir() or not re.fullmatch(r"[A-Za-z_]\w*", function):
        return []
    needle = re.compile(
        rf"(?ms)^[ \t]*([^#;{{}}]*?\b{re.escape(function)}\s*"
        rf"\([^;{{}}]*\))[ \t]*;")
    found: list[HeaderDeclaration] = []
    for header in sorted(include_root.rglob("*.h")):
        try:
            text = header.read_text(errors="replace")
        except OSError:
            continue
        if function not in text:
            continue
        masked = _mask_comments(text)
        for match in needle.finditer(masked):
            prototype = " ".join(match.group(1).split())
            if not prototype or prototype.startswith("typedef "):
                continue
            if not re.match(r"^(?:extern\s+)?[A-Za-z_]\w*[\s*]+", prototype):
                continue  # a call in a macro is not a function declaration
            found.append(HeaderDeclaration(
                header.relative_to(include_root).as_posix(), prototype))
            if not all_variants:
                break
        if len(found) >= max_results:
            break
    return found


def active_declarations(repo: Path, function: str, source: str, target: str, ws: Path):
    """Resolve ordinary prototypes under the real frontend's preprocessor.

    Only include directives are copied from the candidate. No function body,
    candidate prototype, or reference implementation enters the probe.
    """
    import hashlib
    import json
    import subprocess
    import tempfile
    from solver import frontend_check, type_transaction
    includes='\n'.join(re.findall(r'(?m)^[ \t]*#[ \t]*include[^\n]+',source))
    if not includes:
        raise ValueError('active prototype probe requires included headers')
    selected=frontend_check.recipe(str(repo),(repo/'Makefile').read_text(),target)
    with tempfile.TemporaryDirectory(prefix='active-abi-',dir=ws) as directory:
        header=Path(directory)/'headers.c'
        header.write_text(includes+'\n')
        command=[*selected['command'],'-Xclang','-ast-dump=json',str(header)]
        try:
            result=subprocess.run(command,cwd=repo,capture_output=True,text=True,timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('active header ABI probe timed out') from exc
        if result.returncode:
            raise ValueError('active header ABI probe rejected: '+result.stderr[-2000:])
        ast=json.loads(result.stdout)
    def walk(node):
        yield node
        for child in node.get('inner',[]):
            yield from walk(child)
    prototypes=set()
    for node in walk(ast):
        if node.get('kind')!='FunctionDecl' or node.get('name')!=function:
            continue
        spelling=node['type']['qualType']
        returns=spelling.split('(',1)[0].strip()
        parameters=[child['type']['qualType'] for child in node.get('inner',[])
                    if child.get('kind')=='ParmVarDecl']
        if ('...' in spelling or not re.fullmatch(r'[\w\s*]+',returns)
                or any(not re.fullmatch(r'[\w\s*]+',p) and not (
                    re.fullmatch(r'[\w\s*]+\(\s*\*\s*\)\s*\([^()]*\)',p)
                    and type_transaction.signature('void abi_parameter('+p+');','abi_parameter') is not None)
                    for p in parameters)
                or (not parameters and '(void)' not in spelling)):
            raise ValueError('active prototype has unsupported complex/unspecified ABI')
        prototypes.add(returns+' '+function+'('+(', '.join(parameters) or 'void')+')')
    if len(prototypes)!=1:
        raise ValueError('active header ABI requires one unambiguous declaration')
    prototype=next(iter(prototypes))
    report={'kind':'compiler-selected-header-prototype','function':function,
        'prototype':prototype,'includes':includes,'frontend_recipe':selected,
        'header_probe_sha256':hashlib.sha256((includes+'\n').encode()).hexdigest(),
        'selected_prototypes_sha256':hashlib.sha256(json.dumps(sorted(prototypes)).encode()).hexdigest(),
        'reference_body_supplied':False,'candidate_body_supplied':False,
        'authority':'header declaration selected by configured frontend; not binary-only ABI recovery'}
    return [HeaderDeclaration('<compiler-selected headers>',prototype)],report


def _insert_include(source: str, include: str) -> str:
    directive = f'#include "{include}"'
    if directive in source:
        return source
    includes = list(re.finditer(r"^[ \t]*#\s*include[^\n]*(?:\n|$)",
                                source, re.M))
    if includes:
        at = includes[-1].end()
        return source[:at] + directive + "\n" + source[at:]
    return directive + "\n" + source


def _top_level_declarations(source: str):
    """Yield standalone declaration spans, never statements inside bodies.

    This is deliberately a narrow scanner, not a C parser. Unsupported
    declarators remain for the compiler/model; literals and preprocessor lines
    cannot create fake brace or semicolon boundaries.
    """
    masked = _mask_noncode(source)
    masked = re.sub(r"(?m)^[ \t]*#(?:[^\n]*\\\n)*[^\n]*",
                    lambda m: re.sub(r"[^\n]", " ", m.group()), masked)
    linkage = {m.end() - 1 for m in re.finditer(r'\bextern\s+"C"\s*\{', source)}
    braces = []
    depth, start = 0, 0
    for index, char in enumerate(masked):
        if char == "{":
            transparent = index in linkage
            braces.append(transparent)
            if transparent and depth == 0:
                start = index + 1
            else:
                depth += 1
        elif char == "}":
            if not (braces and braces.pop()):
                depth -= 1
            if depth == 0:
                start = index + 1
        elif char == ";" and depth == 0:
            first = start
            while first < index and masked[first].isspace():
                first += 1
            yield first, index + 1, masked[first:index + 1]
            start = index + 1


def _declared_name(declaration: str) -> str | None:
    # One ordinary prototype or one extern object. No initializers, comma
    # object declarations, typedefs, macro declarators, or definitions.
    if re.search(r"\b(?:typedef|static)\b|[={}#]", declaration):
        return None
    function = re.fullmatch(
        r"[\w?\s*]+?\s+\**([A-Za-z_]\w*)\s*\([^;{}]*\)\s*;",
        declaration)
    if function:
        return function.group(1)
    obj = re.fullmatch(
        r"extern\s+[\w?\s*]+?\s+\**([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*;",
        declaration)
    return obj.group(1) if obj else None


def _included_declarations(repo: Path, source: str) -> dict[str, list[str]]:
    root = (Path(repo) / "include").resolve()
    include_re = re.compile(r'(?m)^[ \t]*#\s*include\s*[<"]([^>"\n]+)[>"]')
    stack = [root / inc for inc in include_re.findall(_mask_comments(source))]
    visited: set[Path] = set()
    provided: dict[str, list[str]] = {}
    while stack and len(visited) < 400:
        path = stack.pop().resolve()
        if path in visited or not path.is_relative_to(root) or path.suffix != ".h":
            continue
        visited.add(path)
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        for _, _, decl in _top_level_declarations(text):
            name = _declared_name(decl)
            if name and "?" not in decl:
                provided.setdefault(name, []).append(decl)
        for inc in include_re.findall(_mask_comments(text)):
            relative = path.parent / inc
            stack.append(relative if relative.is_file() else root / inc)
    return provided


def repair_context(repo: Path, source: str, max_chars: int = 8000) -> str:
    """Show referenced ABI declarations from included headers, never C bodies.

    Conditional declarations are labelled syntactic context, not new binary
    evidence or a license to redeclare globals in a candidate. This closes the
    gap where compilation knew a global's type but the repair model did not.
    """
    used = set(re.findall(r"\b[A-Za-z_]\w*\b", _mask_noncode(source)))
    provided = _included_declarations(repo, source)
    rows = []
    size = 0
    for name in sorted(used & provided.keys()):
        for declaration in sorted(set(provided[name])):
            line = " ".join(declaration.split())
            if size + len(line) + 1 > max_chars:
                rows.append("[remaining declarations omitted by context budget]")
                break
            rows.append(line)
            size += len(line) + 1
        if rows and rows[-1].startswith("[remaining"):
            break
    if not rows:
        return ""
    return ("\nREFERENCED INCLUDED-HEADER DECLARATIONS (read-only; not editable in CURRENT C):\n"
            "These are syntactic build context; conditional declarations may be inactive. "
            "Do not invent or edit absent extern declarations. The compiler resolves active types.\n"
            + "\n".join(rows) + "\n")


def reconcile_declarations(repo: Path, source: str) -> tuple[str, list[str]]:
    """Let explicitly included headers own duplicate generated declarations.

    Headers only, never reconstructed C bodies. Conditional declarations are
    syntactic evidence, not proof they are active: every variant still must
    compile and pass the independent oracle. Unknown/unrelated declarations
    and all local declarations remain untouched. The caller retains the
    unreconciled candidate as a separate experiment.
    """
    provided = _included_declarations(repo, source)
    removed = []
    for start, end, declaration in reversed(list(_top_level_declarations(source))):
        name = _declared_name(declaration)
        if name in provided:
            source = source[:start] + source[end:]
            removed.append(name)
    return source, list(reversed(removed))


def infer_address_externs(repo: Path, source: str) -> tuple[str, list[str]]:
    """Compile-context hypotheses for ``knownCall(&unknownExtern, ...)``.

    Only single-level, non-void pointer parameters and flat argument lists are
    supported. Conflicting header/callsite types suppress inference. This does
    not establish object extent or semantics and never defines storage.
    """
    provided = _included_declarations(repo, source)
    unknown = {}
    for start, end, declaration in _top_level_declarations(source):
        match = re.fullmatch(r"extern\s+\?\s+([A-Za-z_]\w*)\s*;", declaration)
        if match:
            unknown[match.group(1)] = (start, end)
    masked = _mask_noncode(source)
    constraints: dict[str, set[str]] = {}
    for call in re.finditer(r"\b([A-Za-z_]\w*)\s*\(([^(){};]*)\)", masked):
        arguments = [arg.strip() for arg in call.group(2).split(",")]
        for declaration in provided.get(call.group(1), []):
            parameters = re.search(r"\(([^()]*)\)\s*;", declaration)
            if not parameters:
                continue
            parameters = parameters.group(1).split(",")
            if len(parameters) != len(arguments):
                continue
            for argument, parameter in zip(arguments, parameters):
                address = re.fullmatch(r"&\s*([A-Za-z_]\w*)", argument)
                if not address or address.group(1) not in unknown:
                    continue
                pointer = re.fullmatch(
                    r"\s*((?:(?:const|volatile)\s+)?(?:struct\s+)?[A-Za-z_]\w*)"
                    r"\s*\*\s*(?:[A-Za-z_]\w*)?\s*", parameter)
                # A void*/complex signature supplies no precise object type.
                if pointer and not re.search(r"\bvoid\b", pointer.group(1)):
                    constraints.setdefault(address.group(1), set()).add(pointer.group(1))
    edits = []
    for name, types in constraints.items():
        if len(types) == 1:
            start, end = unknown[name]
            spelling = next(iter(types))
            edits.append((start, end, f"extern {spelling} {name};", f"{name}={spelling}"))
    for start, end, replacement, _ in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source, [plan for _, _, _, plan in sorted(edits)]


def called_functions(asm: str) -> list[str]:
    """Direct linker symbols called by the target assembly, in first-use order."""
    found: list[str] = []
    for raw in asm.splitlines():
        line = re.sub(r"^\s*/\*.*?\*/\s*", "", raw).strip()
        match = re.match(r"(?:jal|bal)\s+(?:\$?[0-9A-Fa-fx]+\s*,\s*)?"
                         r"([A-Za-z_]\w*)\b", line, re.I)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def context_headers(repo: Path, function: str, asm: str) -> list[str]:
    """Headers declaring the target followed by its direct callees."""
    headers: list[str] = []
    for symbol in [function, *called_functions(asm)]:
        # Alternative declarations can belong to mutually incompatible header
        # environments (e.g. an SDK API and a reconstructed private shim).
        # Include one declaring header, not their union. Other target-header
        # variants remain separately available to the compiler experiment.
        for declaration in declarations(repo, symbol)[:1]:
            if declaration.include not in headers:
                headers.append(declaration.include)
    return headers


def _typedef_definition(text: str, name: str) -> str | None:
    """Return a complete braced typedef definition for ``name`` when present."""
    masked = _mask_comments(text)
    starts = re.finditer(
        r"\btypedef\s+(?:struct|union)\s*([A-Za-z_]\w*)?\s*\{", masked)
    for match in starts:
        opening = masked.find("{", match.start(), match.end())
        depth = 0
        closing = -1
        for index in range(opening, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing < 0:
            continue
        semicolon = masked.find(";", closing)
        if semicolon < 0:
            continue
        tag = match.group(1) or ""
        aliases = masked[closing + 1:semicolon]
        if tag == name or re.search(rf"\b{re.escape(name)}\b", aliases):
            line_start = text.rfind("\n", 0, match.start()) + 1
            return text[line_start:semicolon + 1].strip()
    return None


def relevant_declaration_context(repo: Path, draft: str,
                                 max_chars: int = 14_000) -> str:
    """Extract exact declarations for globals named by a generated draft.

    A draft that invents ``extern Foo *gBar`` can otherwise keep reasoning from
    that false premise even though the reconstructed project already declares
    ``gBar`` as an array.  This reads headers only and places the small,
    directly relevant declarations ahead of broad header context.
    """
    include_root = Path(repo).expanduser() / "include"
    if not include_root.is_dir() or not draft:
        return ""
    names = sorted(set(re.findall(r"\b(g[A-Z][A-Za-z0-9_]*)\b", draft)))
    if not names:
        return ""

    declarations_found: list[tuple[Path, str]] = []
    type_names: list[str] = []
    seen_declarations: set[str] = set()
    for header in sorted(include_root.rglob("*.h")):
        try:
            text = header.read_text(errors="replace")
        except OSError:
            continue
        masked = _mask_comments(text)
        for name in names:
            if name not in text:
                continue
            match = re.search(
                rf"(?ms)^[ \t]*extern\b[^;]*\b{re.escape(name)}\b[^;]*;",
                masked)
            if not match:
                continue
            declaration = text[match.start():match.end()].strip()
            if declaration in seen_declarations:
                continue
            declarations_found.append((header, declaration))
            seen_declarations.add(declaration)
            for token in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", declaration):
                if token not in type_names:
                    type_names.append(token)

    parts: list[str] = []
    used = 0

    def append(header: Path, label: str, block: str) -> None:
        nonlocal used
        rendered = (
            f"HEADER {header.relative_to(include_root).as_posix()} "
            f"({label}):\n{block}\n")
        if used + len(rendered) <= max_chars:
            parts.append(rendered)
            used += len(rendered)

    for header, declaration in declarations_found:
        append(header, "exact declaration", declaration)

    emitted_types: set[str] = set()
    # Prefer definitions in the same header as the declaration.  This captures
    # array element layouts such as RacePlayer without dumping unrelated files.
    for type_name in type_names:
        for header in sorted({item[0] for item in declarations_found}):
            try:
                text = header.read_text(errors="replace")
            except OSError:
                continue
            definition = _typedef_definition(text, type_name)
            if definition:
                append(header, f"definition of {type_name}", definition)
                emitted_types.add(type_name)
                break

    return "\n".join(parts)


def prompt_context(repo: Path, function: str, asm: str, draft: str = "",
                   max_chars: int = 20_000) -> str:
    """Render bounded project declarations without reading target C source."""
    include_root = Path(repo).expanduser() / "include"
    parts: list[str] = []
    focused = relevant_declaration_context(repo, draft, max_chars=max_chars)
    if focused:
        parts.append("RELEVANT DECLARATIONS:\n" + focused)
    remaining = max_chars - sum(len(part) for part in parts)
    for include in context_headers(repo, function, asm):
        path = include_root / include
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        block = f"HEADER {include}:\n{text.strip()}\n"
        if len(block) > remaining:
            block = block[:remaining]
        if not block:
            break
        parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break
    return "\n".join(parts) if parts else "(no project header context found)"


def dependency_headers(repo: Path, draft: str,
                       max_results: int = 24) -> list[str]:
    """Headers defining draft-used types/globals or bare function values.

    A callback passed as an argument does not appear in the target assembly as
    a direct ``jal``.  It is nevertheless ordinary compile context: if a
    reconstructed project header declares that identifier as a function, add
    the header instead of asking a repair model to invent a prototype.
    """
    include_root = Path(repo).expanduser() / "include"
    if not include_root.is_dir():
        return []
    type_names = sorted(set(re.findall(
        r"\b([A-Z][A-Za-z0-9_]*)\s*\*", draft)))
    global_names = sorted(set(re.findall(r"\b(g[A-Z][A-Za-z0-9_]*)\b", draft)))
    bare_arguments = sorted(set(re.findall(
        r"(?:\(|,)\s*&?\s*([A-Za-z_]\w*)\s*(?=,|\))", draft)))
    wanted = [("type", name) for name in type_names]
    wanted += [("global", name) for name in global_names]
    wanted += [("function", name) for name in bare_arguments]
    found: list[str] = []
    satisfied: set[tuple[str, str]] = set()
    for header in sorted(include_root.rglob("*.h")):
        try:
            text = header.read_text(errors="replace")
        except OSError:
            continue
        masked = _mask_comments(text)
        matches = []
        for kind, name in wanted:
            if (kind, name) in satisfied or name not in text:
                continue
            if kind == "type":
                defines = re.search(
                    rf"\b(?:typedef\s+)?(?:struct|union)\s+"
                    rf"{re.escape(name)}\s*\{{", masked)
            elif kind == "global":
                defines = re.search(
                    rf"(?ms)^\s*extern\b[^;]*\b{re.escape(name)}\b[^;]*;",
                    masked)
            else:
                defines = re.search(
                    rf"(?ms)^\s*[^#;{{}}]*\b{re.escape(name)}\s*"
                    rf"\([^;{{}}]*\)\s*;", masked)
            if defines:
                matches.append((kind, name))
        if matches:
            found.append(header.relative_to(include_root).as_posix())
            satisfied.update(matches)
            if len(found) >= max_results:
                break
    return found


def is_empty_return_asm(asm: str, function: str) -> bool:
    """True only for the function body ``jr $ra`` / ``nop``."""
    body = re.search(
        rf"(?ms)^[ \t]*glabel\s+{re.escape(function)}\s*$"
        rf"(.*?)^[ \t]*endlabel\s+{re.escape(function)}\s*$", asm)
    text = body.group(1) if body else asm
    opcodes = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*/\*.*?\*/\s*", "", raw).strip()
        if (not line or line.endswith(":") or line.startswith((".", "\\"))
                or line.startswith(("glabel ", "endlabel ", "nonmatching "))):
            continue
        match = re.match(r"([A-Za-z][A-Za-z0-9.]*)\b", line)
        if match:
            opcodes.append(match.group(1).lower())
    return opcodes == ["jr", "nop"]


def preflight_variants(repo: Path, function: str, asm: str,
                       draft: str) -> list[tuple[str, str]]:
    """Return deterministic header/body variants, with no target-source read."""
    decls = declarations(repo, function)
    variants: list[tuple[str, str]] = []
    seen = {draft}
    for decl in decls:
        source = _insert_include(draft, decl.include)
        if source not in seen:
            variants.append((f"project_header:{decl.include}", source))
            seen.add(source)

    all_headers = context_headers(repo, function, asm)
    for include in dependency_headers(repo, draft):
        if include not in all_headers:
            all_headers.append(include)
    source = draft
    for include in all_headers:
        source = _insert_include(source, include)
    if all_headers and source not in seen:
        variants.append((f"project_call_context:{len(all_headers)}", source))
        seen.add(source)

    reconciled, removed = reconcile_declarations(repo, source)
    if removed and reconciled not in seen:
        variants.append(("project-header-reconcile:" + ",".join(removed), reconciled))
        seen.add(reconciled)
    inferred, plans = infer_address_externs(repo, reconciled)
    if plans and inferred not in seen:
        variants.append(("project-header-address-extern:" + ",".join(plans), inferred))
        seen.add(inferred)

    if is_empty_return_asm(asm, function):
        empty_decls = [decl for decl in decls
                       if re.match(r"^void\b", decl.prototype)]
        if not empty_decls:
            source = (f'#include "common.h"\n\n'
                      f"void {function}(void) {{\n}}\n")
            if source not in seen:
                variants.append(("binary_empty_return", source))
        for decl in empty_decls:
            source = _insert_include('#include "common.h"\n', decl.include)
            source += f"\n{decl.prototype} {{\n}}\n"
            if source not in seen:
                variants.append((
                    f"binary_empty_return:{decl.include}", source))
                seen.add(source)
    return variants

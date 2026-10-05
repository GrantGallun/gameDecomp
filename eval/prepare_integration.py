"""Prepare isolated function-only TU replacements from scoped certificates.

Reference TU bodies are opaque replacement destinations, never solver inputs.
Declaration-only externs are retained and checked with the project's frontend.
Definitions, macros and assembly TUs require an explicit integration backend;
this preparer refuses to guess at them. The real repository is read-only.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from eval.integration_gate import inside, sha
from solver import c89, function_boundary


def definition_span(source: str, function: str) -> tuple[int, int]:
    masked = c89._mask(source)
    # Preprocessor conditionals can contain mutually exclusive duplicate
    # definitions. Decline ambiguity instead of selecting by textual order.
    pattern = re.compile(rf"(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t\r\n*]+)+"
                         rf"{re.escape(function)}\s*\([^;{{}}]*\)\s*\{{")
    matches = list(pattern.finditer(masked))
    if len(matches) != 1:
        raise ValueError(f"{function}: expected one ordinary C definition, found {len(matches)}")
    match = matches[0]
    depth, end = 1, match.end()
    while depth and end < len(masked):
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    if depth:
        raise ValueError("unbalanced function body")
    return match.start(), end


# `type name(params);` only: a declarator such as `void (*callback)(void);` is an object, not a prototype.
PROTOTYPE = re.compile(r"(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t*]+)+[A-Za-z_]\w*[ \t]*"
                       r"\([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*\)[ \t]*;")
NOT_PROTOTYPE = re.compile(r"(?:static|inline|typedef|extern|register|auto|return|goto|case|else|do)\b")


_DEFINE = re.compile(r"(?m)^[ \t]*#[ \t]*define[ \t]+(?P<name>[A-Za-z_]\w*)(?P<body>[ \t]+[A-Za-z_0-9][\w ]*)?[ \t]*$")
_TYPEDEF_HEAD = re.compile(r"\btypedef\s+(?:struct|union|enum)\b[^;{}]*\{")
_STRING_OBJECT_MASKED = re.compile(r"(?m)^[ \t]*const[ \t]+char[ \t]+([A-Za-z_]\w*)[ \t]*\[[^\]\n]*\][ \t]*=[ \t]+;")
_STRING_OBJECT = re.compile(r'const\s+char\s+(?P<name>[A-Za-z_]\w*)\s*\[\s*(?:0[xX][0-9A-Fa-f]+|\d+)?\s*\]\s*=\s*"(?:[^"\\\n]|\\.)*"\s*;')
_TYPEDEF_NAME = re.compile(r"\}\s*([A-Za-z_]\w*)\s*;")


def _extract_preamble(stripped: str, masked: str) -> tuple[list[str], str]:
    """Pull compile-time-only file-scope text out of `masked`: brace typedefs and object-like `#define`s.

    Returns (blocks, masked with those spans blanked). A typedef is admitted only as
    `typedef struct|union|enum [Tag] { ... } Name;`; function typedefs, pointer/array aliases and bare
    `struct Foo {...};` stay unadmitted and therefore block. None of this text emits code or data, but it
    is still a candidate obligation: replace_function yields or declines on conflict, and the whole-ROM
    gate decides the rest.
    """
    found = []
    while True:
        head = _TYPEDEF_HEAD.search(masked)
        if not head:
            break
        depth, end = 1, head.end()
        while depth and end < len(masked):
            depth += (masked[end] == "{") - (masked[end] == "}")
            end += 1
        tail = _TYPEDEF_NAME.match(masked, end - 1)
        if depth or not tail:
            break                                # leave it in the remainder so the caller declines
        found.append((head.start(), stripped[head.start():tail.end()].strip()))
        masked = masked[:head.start()] + " " * (tail.end() - head.start()) + masked[tail.end():]
    for match in list(_DEFINE.finditer(masked)):
        if not _DEFINE.fullmatch(stripped[match.start():match.end()]):
            continue                             # the body held a string or comment the mask blanked: not an alias
        found.append((match.start(), stripped[match.start():match.end()].strip()))
        masked = masked[:match.start()] + " " * (match.end() - match.start()) + masked[match.end():]
    # A TU-owned string object: it emits .rodata, so unlike the text above it is data. Only the plain
    # `const char NAME[N] = "literal";` shape is admitted; placement is proven by the whole-ROM gate, not here.
    for match in list(_STRING_OBJECT_MASKED.finditer(masked)):
        if not _STRING_OBJECT.fullmatch(stripped[match.start():match.end()].strip()):
            continue
        found.append((match.start(), stripped[match.start():match.end()].strip()))
        masked = masked[:match.start()] + " " * (match.end() - match.start()) + masked[match.end():]
    return [text for _, text in sorted(found)], masked


def _candidate_split(source: str, function: str) -> tuple[str, list[str], list[str], list[str]]:
    """`_candidate_components` plus the admitted file-scope typedefs and `#define`s."""
    if re.search(r"\b(?:asm|__asm|__asm__|INCLUDE_ASM|GLOBAL_ASM)\b", c89._mask(source)):
        raise ValueError("assembly is not eligible for C integration")
    start, end = definition_span(source, function)
    outside = source[:start] + source[end:]
    includes = re.findall(r'(?m)^[ \t]*#include[ \t]+"([A-Za-z0-9_./-]+\.h)"[ \t]*$', outside)
    stripped = re.sub(r'(?m)^[ \t]*#include[ \t]+"[A-Za-z0-9_./-]+\.h"[ \t]*$', "", outside)
    preamble, masked = _extract_preamble(stripped, c89._mask(stripped))
    declarations = []
    # Extraction is not admission: prepare() independently checks these exact
    # declarations with Clang under the real project headers before replacement.
    for match in re.finditer(r"\bextern\s+[^;{}=#]+;", masked):
        declarations.append(stripped[match.start():match.end()].strip())
    remaining = re.sub(r"\bextern\s+[^;{}=#]+;", lambda m: " " * len(m.group(0)), masked)
    # A plain prototype declares exactly what `extern` spells out: a function has
    # external linkage by default. m2c emits them (`void drawMainMenuSceneModel(void *);`);
    # on 2026-09-14 they alone kept 73 exact candidates out of integration.
    for match in PROTOTYPE.finditer(remaining):
        text = stripped[match.start():match.end()].strip()
        if NOT_PROTOTYPE.match(text):
            continue
        declarations.append("extern " + text)
        remaining = remaining[:match.start()] + " " * (match.end() - match.start()) + remaining[match.end():]
    if remaining.strip():
        raise ValueError("candidate needs shared declaration integration; only includes, declaration-only externs and one function supported")
    return source[start:end], list(dict.fromkeys(includes)), list(dict.fromkeys(declarations)), list(dict.fromkeys(preamble))


def _candidate_components(source: str, function: str) -> tuple[str, list[str], list[str]]:
    """Body, includes and extern declarations; file-scope typedefs/macros are not admitted on this path."""
    body, includes, declarations, preamble = _candidate_split(source, function)
    if preamble:
        raise ValueError("candidate needs shared declaration integration; only includes, declaration-only externs and one function supported")
    return body, includes, declarations


def candidate_parts(source: str, function: str) -> tuple[str, list[str]]:
    """Context-free readiness check; externs need prepare()'s AST admission."""
    body, includes, declarations = _candidate_components(source, function)
    if declarations:
        raise ValueError("extern declarations require contextual frontend admission")
    return body, includes


def check_extern_declarations(*, repo: Path, target: str, includes: list[str], declarations: list[str],
                              preamble: list[str] = ()) -> dict:
    """Admit declaration-only externs with actual header types, not a regex ABI."""
    from solver import frontend_check
    if not declarations and not preamble:
        return {"kind": "integration-extern-declarations", "declarations": []}
    selected = frontend_check.recipe(str(repo), (repo / "Makefile").read_text(), target)
    prefix = "".join(f'#include "{name}"\n' for name in includes) + "".join(block + "\n" for block in preamble)
    probe, offsets = prefix, []
    for declaration in declarations:
        offsets.append(len(probe.encode()))
        probe += declaration + "\n"
    with tempfile.TemporaryDirectory(prefix="integration-externs-") as directory:
        path = Path(directory) / "declarations.c"
        path.write_text(probe, encoding="utf-8")
        process = subprocess.run([*selected["command"], "-Xclang", "-ast-dump=json", str(path)],
                                 cwd=repo, capture_output=True, text=True, timeout=60)
        if process.returncode:
            raise ValueError("extern declaration frontend rejected: " + process.stderr[-2000:])
        ast = json.loads(process.stdout)
        if ast.get("kind") != "TranslationUnitDecl":
            raise ValueError("unexpected extern declaration AST")
        admitted = []
        for declaration, offset in zip(declarations, offsets):
            matches = [node for node in ast.get("inner", [])
                       if node.get("range", {}).get("begin", {}).get("offset") == offset
                       and "includedFrom" not in node.get("range", {}).get("begin", {})
                       and "includedFrom" not in node.get("loc", {})]
            if len(matches) != 1:
                raise ValueError("extern declaration requires one ordinary AST declaration")
            node = matches[0]
            begin, end = node["range"]["begin"], node["range"]["end"]
            if (node.get("kind") not in {"VarDecl", "FunctionDecl"}
                    or node.get("storageClass") != "extern" or node.get("init")
                    or node.get("inline") or node.get("isImplicit")
                    or any(child.get("kind") != "ParmVarDecl" for child in node.get("inner", []))
                    or any(key in loc for loc in (begin, end, node.get("loc", {}))
                           for key in ("spellingLoc", "expansionLoc", "includedFrom"))
                    or any(loc.get("file", str(path)) != str(path) for loc in (begin, end, node.get("loc", {})))
                    or end.get("offset", -1) + end.get("tokLen", 0) != offset + len(declaration.rstrip().removesuffix(';').encode())):
                raise ValueError("unsupported extern definition, expansion or attribute")
            admitted.append({"name": node["name"], "kind": node["kind"], "type": node["type"]["qualType"],
                             "canonical_type": node["type"].get("desugaredQualType", node["type"]["qualType"]),
                             "source_sha256": sha(declaration.encode())})
    return {"kind": "integration-extern-declarations", "declarations": admitted,
            "probe_sha256": sha(probe.encode()), "frontend_recipe": selected,
            "scope": "declaration syntax and active header compatibility; whole-TU/ROM build still required"}


def destination_header_includes(source: str) -> list[str]:
    """Literal, unconditional includes only; source-local header selection declines."""
    masked = c89._mask(source)
    depth, offset, local_defines, brace_depth = 0, 0, False, 0
    includes = []
    for line in source.splitlines(keepends=True):
        visible = masked[offset:offset + len(line)]
        offset += len(line)
        previous_brace_depth = brace_depth
        brace_depth += visible.count('{') - visible.count('}')
        directive = re.match(r'^[ \t]*#[ \t]*(\w+)\b', visible)
        if not directive:
            continue
        kind = directive[1]
        if kind in ('if', 'ifdef', 'ifndef'):
            depth += 1
        elif kind == 'endif':
            depth -= 1
        elif kind in ('define', 'undef'):
            local_defines = True
        elif kind == 'include':
            if previous_brace_depth:
                continue                       # data initializer or function include, not header scope
            if depth:
                raise ValueError('conditional destination header requires explicit integration context')
            if local_defines:
                raise ValueError('source-local header selection requires explicit integration context')
            literal = re.match(r'^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"', line)
            if not literal or '\\' in line:
                raise ValueError('unsupported destination header include')
            if literal[1].endswith('.h'):
                includes.append(literal[1])
    return list(dict.fromkeys(includes))


def header_object_conflicts(*, repo: Path, target: str, includes: list[str], admission: dict) -> dict:
    """Measure candidate object-type conflicts in active destination headers only.

    No destination function body or initializer is supplied to the checker. A
    conflict permits the existing candidate-owned typed access, never copying
    header layouts into the candidate. Equal and absent objects stay untouched.
    Function prototypes retain their existing, separate integration policy.
    """
    objects = {r['name']: r for r in admission['declarations'] if r['kind'] == 'VarDecl'}
    report = {'kind': 'integration-header-object-conflicts', 'conflicts': [],
              'reference_body_supplied': False, 'candidate_body_supplied': False}
    if not objects or not includes:
        return report
    from solver import frontend_check
    selected = frontend_check.recipe(str(repo), (repo / 'Makefile').read_text(), target)
    probe = ''.join(f'#include "{name}"\n' for name in dict.fromkeys(includes))
    with tempfile.TemporaryDirectory(prefix='integration-header-objects-') as directory:
        path = Path(directory) / 'headers.c'
        path.write_text(probe, encoding='utf-8')
        process = subprocess.run([*selected['command'], '-Xclang', '-ast-dump=json', str(path)],
                                 cwd=repo, capture_output=True, text=True, timeout=60)
        if process.returncode:
            raise ValueError('header object probe rejected: ' + process.stderr[-2000:])
        ast = json.loads(process.stdout)
        if ast.get('kind') != 'TranslationUnitDecl':
            raise ValueError('unexpected header object AST')
        active = {}
        for node in ast.get('inner', []):
            name = node.get('name')
            if name in objects and node.get('kind') == 'VarDecl':
                ctype = node['type'].get('desugaredQualType', node['type']['qualType'])
                active.setdefault(name, set()).add(ctype)
        for name, types in sorted(active.items()):
            if len(types) != 1:
                raise ValueError('ambiguous active header object: ' + name)
            own = objects[name].get('canonical_type', objects[name]['type'])
            if own != next(iter(types)):
                report['conflicts'].append(name)
        report.update(probe_sha256=sha(probe.encode()), frontend_recipe=selected,
                      active_types={name: sorted(types) for name, types in sorted(active.items())})
    return report


def _declared_name(declaration: str) -> str:
    """The identifier a prototype declares: the last name before its parameter list."""
    head = declaration.split("(", 1)[0]
    names = re.findall(r"[A-Za-z_]\w*", head)
    return names[-1] if names else ""


_EXTERN = re.compile(r"^\s*extern\s+(?P<type>(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*[A-Za-z_]\w*"
                     r"(?:\s*\*)*)\s*(?P<name>[A-Za-z_]\w*)\s*(?P<rest>\[[^\]]*\]|\([^()]*\))?\s*;\s*$", re.S)


def _conflicts(destination_masked: str, declaration: str) -> bool:
    """True when the destination file itself declares the symbol with a different type than the candidate's extern."""
    m = _EXTERN.match(declaration)
    if not m:
        return False
    name = m["name"]
    norm = lambda t: " ".join(t.replace("*", " * ").split())
    for d in re.finditer(rf"^[ \t]*(?:extern\s+|static\s+)?(?P<type>(?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*"
                         rf"[A-Za-z_]\w*(?:\s*\*)*)\s*{re.escape(name)}\s*(?P<rest>\[[^\]]*\]|\([^()]*\))?\s*[;=]",
                         destination_masked, re.M):
        if norm(d["type"]) in ("return", "else", "case", "goto"):
            continue
        cand_kind = "fn" if (m["rest"] or "").startswith("(") else "arr" if m["rest"] else "obj"
        dest_kind = "fn" if (d["rest"] or "").startswith("(") else "arr" if d["rest"] else "obj"
        if norm(d["type"]) != norm(m["type"]) or cand_kind != dest_kind:
            return True
        return cand_kind == "fn" and _param_types(m["rest"]) != _param_types(d["rest"])
    return False


def _param_types(params: str) -> list[str]:
    """`(s32 x, const char *text)` -> ['s32', 'const char *']: parameter names dropped, spacing normalised."""
    inner = params.strip()[1:-1].strip()
    if inner in ("", "void"):
        return []
    out = []
    for p in inner.split(","):
        toks = p.replace("*", " * ").split()
        if len(toks) > 1 and re.fullmatch(r"[A-Za-z_]\w*", toks[-1]) and toks[-1] not in (
                "int", "char", "short", "long", "unsigned", "signed", "void", "float", "double") \
                and not re.fullmatch(r"[su](?:8|16|32|64)|f(?:32|64)", toks[-1]):
            toks = toks[:-1]
        out.append(" ".join(toks))
    return out


def _typed_uses(body: str, declaration: str) -> str | None:
    """`body` with every use of the declared symbol stated in the declaration's own type, or None if unsupported.

    Scalar `extern T x;` -> `(*(T *)&x)` (the same load/store width and signedness, as a value or an lvalue);
    array `extern T x[];` -> `((T *)x)`; function: body unchanged (the prototype only yields). Function pointers,
    shadowing locals and anything not matching the plain shapes decline, and the declaration is then kept.
    """
    m = _EXTERN.match(declaration)
    if not m:
        return None
    ctype, name, rest = " ".join(m["type"].split()), m["name"], m["rest"]
    masked = c89._mask(body)
    brace = masked.find("{")
    if brace < 0 or re.search(rf"\b{re.escape(name)}\b", masked[:brace]):
        return None                           # the symbol is the function's own name or appears in its parameters
    if re.search(rf"^[ \t]*[A-Za-z_][\w \t\*]*\b{re.escape(name)}\s*(?:\[[^\]]*\])?\s*[;=]", masked[brace:], re.M):
        return None                           # a local of the same name shadows it
    if rest is None:
        replacement = f"(*({ctype} *)&{name})"
    elif rest.startswith("["):
        replacement = f"(({ctype} *){name})"
    else:
        # Functions are never cast at the call: a call through a cast pointer can compile to jalr instead of jal.
        # The candidate's prototype yields and the call uses the destination's; the ROM check decides.
        return body
    pattern = re.compile(rf"(?<![\w.>]){re.escape(name)}\b")
    hits = [mm for mm in pattern.finditer(masked) if mm.start() > brace]
    if not hits:
        return body                           # declared but unused: yielding is enough
    out = body
    for mm in reversed(hits):
        out = out[:mm.start()] + replacement + out[mm.end():]
    return out


def _preamble_name(block: str) -> str:
    if block.lstrip().startswith("#"):
        return _DEFINE.match(block)["name"]
    if block.lstrip().startswith("const"):
        return _STRING_OBJECT.fullmatch(block)["name"]
    return _TYPEDEF_NAME.search(block[block.rfind("}"):])[1]


def _squash(text: str) -> str:
    return " ".join(text.split())


def _admit_preamble(destination_masked: str, destination: str, preamble: list[str]) -> list[str]:
    """Candidate typedefs/macros the destination TU lacks. An identical one yields; a different one of the same name declines.

    Nothing is copied from the destination and no type is reconciled: a same-named, different layout would change what
    the candidate's body means, so it is refused rather than merged.
    """
    needed = []
    for block in preamble:
        name = _preamble_name(block)
        if _squash(block) in _squash(destination):
            continue
        if block.lstrip().startswith("#"):
            clash = re.search(rf"(?m)^[ \t]*#[ \t]*define[ \t]+{re.escape(name)}\b", destination_masked)
        elif block.lstrip().startswith("const"):
            # any prior mention of the object as a declarator, definition or extern, even an equal one, declines
            clash = re.search(rf"(?m)^[ \t]*(?:extern\s+|static\s+)?(?:const\s+)?[A-Za-z_][\w \t*]*\b{re.escape(name)}\s*(?:\[[^\]]*\])?\s*[;=]",
                              destination_masked)
        else:
            clash = re.search(rf"\}}\s*{re.escape(name)}\s*;|\btypedef\b[^;{{}}]*\b{re.escape(name)}\s*;", destination_masked)
        if clash:
            raise ValueError(f"candidate {name} conflicts with a different definition in the destination translation unit")
        needed.append(block)
    return needed


def replace_function(original: str, candidate: str, function: str, *, header_conflicts=()) -> str:
    body, includes, declarations, preamble = _candidate_split(candidate, function)
    start, end = definition_span(original, function)
    # An unrelated conditional elsewhere in the TU does not make this unique,
    # unconditional definition ambiguous. Preserve it byte-for-byte. Do not
    # select definitions inside conditionals without a preprocessor-aware path.
    depth = 0
    for directive in re.finditer(r"(?m)^[ \t]*#[ \t]*(if|ifdef|ifndef|else|elif|endif)\b", c89._mask(original)):
        if directive.start() >= start:
            break
        if directive.group(1) in {"if", "ifdef", "ifndef"}:
            depth += 1
        elif directive.group(1) == "endif":
            depth -= 1
        elif depth == 0:
            raise ValueError("unbalanced preprocessor conditional")
        if depth < 0:
            raise ValueError("unbalanced preprocessor conditional")
    if depth:
        raise ValueError("conditional function requires explicit integration manifest")
    # A declaration converted from a plain m2c prototype yields whenever the destination
    # TU already names that function: the TU compiled with it in scope (its own
    # declaration, a later definition, or a header). m2c guesses parameter types
    # (`void *`), and redeclaring is a conflicting-types error (finishTrainingCourse,
    # 2026-09-14). Explicit candidate externs are kept as written.
    destination = c89._mask(original)
    masked_candidate = c89._mask(candidate)
    declarations = [d for d in declarations
                    if d in masked_candidate or not re.search(rf"\b{re.escape(_declared_name(d))}\b", destination)]
    # Explicit candidate externs yield the same way when the destination already names the symbol, but the
    # candidate's own type obligation is kept: its uses in the body are rewritten to state that type
    # (`(*(s8 *)&g)`, `((char *)fmt)`); a conflicting function prototype only yields, and calls are left as
    # written. Nothing is copied from the destination. Measured 2026-09-30: 12 of 22 pending-integration build failures were redeclaration or
    # conflicting-type errors on byte-exact functions (5 on one global, `gFramebufferSwapHold` s8 vs u8).
    # Only an actual conflict is rewritten: the destination file itself declares the symbol with a different type.
    # Rewriting every overlap changed codegen for two already-integrated functions in the 2026-09-30 dry run
    # (checksum mismatch), so an equal or header-only declaration keeps the candidate's extern exactly as before.
    kept = []
    for declaration in declarations:
        name = _declared_name(declaration)
        rewritten = _typed_uses(body, declaration) if name and (name in header_conflicts or
                     _conflicts(destination, declaration)) else None
        if rewritten is None:
            kept.append(declaration)
        else:
            body = rewritten
    declarations = kept
    needed = _admit_preamble(destination, original, preamble)
    declaration_text = "\n".join([*needed, *declarations]) + "\n" if needed or declarations else ""
    result = original[:start] + declaration_text + body + original[end:]
    existing = set(re.findall(r'#\s*include\s*"([^"]+)"', original))
    missing = [name for name in includes if name not in existing]
    # Keep the candidate's own linkage/type obligations. Do not infer or copy
    # replacement declarations from the destination's reference implementation.
    return "".join(f'#include "{name}"\n' for name in missing) + result


def prepare(*, repo: Path, db: Path, entries: list[dict], output_dir: Path,
            reference_rom: str = "snowboardkids.z64", built_rom: str = "build/snowboardkids.z64") -> Path:
    if output_dir.exists():
        raise ValueError("refusing to overwrite integration preparation")
    grouped, lineage, names = {}, [], set()
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        for item in entries:
            function = item["function"]
            if function in names or not re.fullmatch(r"[A-Za-z_]\w*", function):
                raise ValueError("duplicate or invalid function")
            names.add(function)
            candidate = Path(item["source"]).read_text(encoding="utf-8")
            certificate = item["verification"]
            candidate_hash = certificate.get("candidate_source_sha256", certificate.get("source_sha256"))
            boundary = certificate.get("function_boundary", {})
            boundary_ok = False
            if certificate.get("exact") is not True and boundary.get("function_exact") is True:
                boundary_ok = (boundary.get("function") == function
                    and function_boundary.revalidate(boundary)
                    and boundary["inputs"]["candidate"]["sha256"] == certificate.get("candidate_sha256")
                    and boundary["inputs"]["target"]["sha256"] == certificate.get("target_sha256")
                    and boundary["inputs"]["rom"]["sha256"] == sha(inside(repo, reference_rom).read_bytes())
                    and sha(Path(boundary["inputs"]["candidate"]["path"]).with_suffix(".c").read_text(encoding="utf-8").encode())
                        == certificate.get("source_sha256"))
                metadata = conn.execute("SELECT addr,size FROM functions WHERE name=?", (function,)).fetchone()
                boundary_ok = boundary_ok and metadata == (boundary.get("address"), boundary.get("size"))
            if (certificate.get("exact") is not True and not boundary_ok) or candidate_hash != sha(candidate.encode()):
                raise ValueError("source-bound exact certificate required")
            for path, digest in certificate.get("build_inputs", {}).items():
                if sha(Path(path).read_bytes()) != digest:
                    raise ValueError("certificate build input changed: " + path)
            row = conn.execute("SELECT f.name,a.source_code,t.name FROM attempts a JOIN functions f "
                "ON a.func_addr=f.addr JOIN tus t ON f.tu_id=t.id WHERE a.id=?", (item["attempt_id"],)).fetchone()
            if row is None or row[0] != function or row[1] != candidate:
                raise ValueError("attempt lineage does not identify exact candidate")
            path = row[2]
            if path.startswith("build/") and path.endswith(".o"):
                path = path[6:-2] + ".c"
            if not path.startswith("src/") or not path.endswith(".c"):
                raise ValueError("unsupported translation unit path")
            _, includes, declarations, preamble = _candidate_split(candidate, function)
            for include in includes:
                if not inside(repo / "include", include).is_file():
                    raise ValueError("candidate include is not a project header")
            target = "build/" + path[:-2] + ".o"
            declaration_check = check_extern_declarations(repo=repo, target=target, includes=includes, declarations=declarations,
                                                        preamble=preamble)
            original = inside(repo, path).read_bytes()
            base, current = grouped.get(path, (original, original.decode("utf-8")))
            try:
                destination_includes = destination_header_includes(current) if any(
                    r['kind'] == 'VarDecl' for r in declaration_check['declarations']) else []
                header_check = header_object_conflicts(repo=repo, target=target,
                                                      includes=destination_includes, admission=declaration_check)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                # Unavailable context declines this additional rewrite, not the
                # previously supported preparation. The whole-TU/ROM gate still
                # rejects any unresolved conflict, and the gap stays auditable.
                header_check = {'kind': 'integration-header-object-conflicts', 'conflicts': [],
                                'status': 'unavailable', 'error': str(exc),
                                'reference_body_supplied': False, 'candidate_body_supplied': False}
            grouped[path] = (base, replace_function(current, candidate, function,
                                                   header_conflicts=header_check['conflicts']))
            lineage.append({"function": function, "attempt_id": item["attempt_id"],
                            "source_sha256": sha(candidate.encode()), "verification": certificate,
                            "admission_scope": "object_sections" if certificate.get("exact") else "function_extent_only",
                            **({"extern_declarations": declaration_check} if declarations or preamble else {}),
                            **({"header_object_conflicts": header_check} if header_check['conflicts'] or
                               header_check.get('status') == 'unavailable' else {}),
                            **({"preamble": preamble} if preamble else {})})
    if not grouped:
        raise ValueError("no exact candidates to integrate")
    output_dir.mkdir(parents=True)
    replacements = []
    for index, (path, (base, source)) in enumerate(sorted(grouped.items())):
        filename = f"{index:03d}.c"
        data = source.encode("utf-8")
        (output_dir / filename).write_bytes(data)
        replacements.append({"path": path, "base_sha256": sha(base), "replacement": filename,
                             "replacement_sha256": sha(data)})
    manifest = {"kind": "exact-function-integration", "reference_rom": reference_rom,
                "reference_sha256": sha(inside(repo, reference_rom).read_bytes()),
                "built_rom": built_rom, "replacements": replacements, "lineage": lineage,
                "scope": "selected function replacements; remaining game is not an autonomous C recovery"}
    path = output_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path

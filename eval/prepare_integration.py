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


def _candidate_components(source: str, function: str) -> tuple[str, list[str], list[str]]:
    if re.search(r"\b(?:asm|__asm|__asm__|INCLUDE_ASM|GLOBAL_ASM)\b", c89._mask(source)):
        raise ValueError("assembly is not eligible for C integration")
    start, end = definition_span(source, function)
    outside = source[:start] + source[end:]
    includes = re.findall(r'(?m)^[ \t]*#include[ \t]+"([A-Za-z0-9_./-]+\.h)"[ \t]*$', outside)
    stripped = re.sub(r'(?m)^[ \t]*#include[ \t]+"[A-Za-z0-9_./-]+\.h"[ \t]*$', "", outside)
    masked = c89._mask(stripped)
    declarations = []
    # Extraction is not admission: prepare() independently checks these exact
    # declarations with Clang under the real project headers before replacement.
    for match in re.finditer(r"\bextern\s+[^;{}=#]+;", masked):
        declarations.append(stripped[match.start():match.end()].strip())
    remaining = re.sub(r"\bextern\s+[^;{}=#]+;", "", masked)
    if remaining.strip():
        raise ValueError("candidate needs shared declaration integration; only includes, declaration-only externs and one function supported")
    return source[start:end], list(dict.fromkeys(includes)), list(dict.fromkeys(declarations))


def candidate_parts(source: str, function: str) -> tuple[str, list[str]]:
    """Context-free readiness check; externs need prepare()'s AST admission."""
    body, includes, declarations = _candidate_components(source, function)
    if declarations:
        raise ValueError("extern declarations require contextual frontend admission")
    return body, includes


def check_extern_declarations(*, repo: Path, target: str, includes: list[str], declarations: list[str]) -> dict:
    """Admit declaration-only externs with actual header types, not a regex ABI."""
    from solver import frontend_check
    if not declarations:
        return {"kind": "integration-extern-declarations", "declarations": []}
    selected = frontend_check.recipe(str(repo), (repo / "Makefile").read_text(), target)
    prefix = "".join(f'#include "{name}"\n' for name in includes)
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
                             "source_sha256": sha(declaration.encode())})
    return {"kind": "integration-extern-declarations", "declarations": admitted,
            "probe_sha256": sha(probe.encode()), "frontend_recipe": selected,
            "scope": "declaration syntax and active header compatibility; whole-TU/ROM build still required"}


def replace_function(original: str, candidate: str, function: str) -> str:
    body, includes, declarations = _candidate_components(candidate, function)
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
    declaration_text = "\n".join(declarations) + "\n" if declarations else ""
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
            _, includes, declarations = _candidate_components(candidate, function)
            for include in includes:
                if not inside(repo / "include", include).is_file():
                    raise ValueError("candidate include is not a project header")
            target = "build/" + path[:-2] + ".o"
            declaration_check = check_extern_declarations(repo=repo, target=target, includes=includes, declarations=declarations)
            original = inside(repo, path).read_bytes()
            base, current = grouped.get(path, (original, original.decode("utf-8")))
            grouped[path] = (base, replace_function(current, candidate, function))
            lineage.append({"function": function, "attempt_id": item["attempt_id"],
                            "source_sha256": sha(candidate.encode()), "verification": certificate,
                            "admission_scope": "object_sections" if certificate.get("exact") else "function_extent_only",
                            **({"extern_declarations": declaration_check} if declarations else {})})
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

"""Bounded source-independent m2c candidates from binary-derived contexts.

No base.c, ctx.c, reference source or game header is read. Callers must compile,
log and retain/reject every returned candidate through their ordinary scorer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from solver import binary_type_context, m2c_input

Unavailable = binary_type_context.Unavailable
CLEAN_PRELUDE = ('#include "include_asm.h"\n#include "compiler_diagnostics.h"\n#include <PR/mbi.h>\n'
                 'int sprintf(char *buffer, const char *format, ...);\n')
CPP_FLAGS = ("-Iinclude", "-Iinclude/PR", "-I.", "-DLANGUAGE_C", "-D_LANGUAGE_C", "-D_MIPS_SZLONG=32",
             "-DNDEBUG", "-DM2CTX", "-DCOMPILING_LIBULTRA", "-DBUILD_VERSION=VERSION_I", "-DF3DEX_GBI")


def code_digest():
    digest = hashlib.sha256(binary_type_context.algorithm_digest().encode())
    for name in ("binary_type_draft.py", "m2c_input.py", "m2c_byte_view.py", "m2c_pointer_return.py",
                 "repair_context.py", "m2c_placeholders.py"):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()[:16]


def input_paths(repo: Path) -> dict[str, Path]:
    """Files that can change the clean m2c context or the target it explains.

    Keys are logical names, independent of the root used by isolated workers.
    The public include tree is intentionally narrower than the game's headers.
    """
    repo = Path(repo)
    paths = {}
    for path in sorted((repo / "build").glob("*.elf")):
        if path.is_file():
            paths[f"elf:{path.name}"] = path
    for name in ("include_asm.h", "compiler_diagnostics.h"):
        path = repo / "include" / name
        if path.is_file():
            paths[f"public:{name}"] = path
    public_pr = repo / "include" / "PR"
    for path in sorted(public_pr.rglob("*.h")) if public_pr.is_dir() else ():
        if path.is_file():
            paths[f"public:PR/{path.relative_to(public_pr).as_posix()}"] = path
    launcher = repo / ".venv" / "bin" / "m2c"
    if launcher.is_file():
        paths["m2c:executable"] = launcher
    for lib in (repo / ".venv" / "lib", repo / ".venv" / "Lib"):
        for site in sorted(lib.glob("python*/site-packages")) if lib.is_dir() else ():
            for package in ("m2c", "m2c_pycparser"):
                root = site / package
                if not root.is_dir():
                    continue
                for path in sorted(root.rglob("*.py")):
                    if path.is_file():
                        rel = path.relative_to(root).as_posix()
                        paths[f"m2c-python:{site.parent.name}/{package}/{rel}"] = path
    return paths


def input_hashes(repo: Path, pins: dict[str, str] | None = None) -> dict[str, str]:
    """Hash actual inputs, or use only a frozen resolved-path hash map.

    A missing pin differs from a missing file. This matters when an ELF is
    added after a prior decline, or a worker lacks an input the controller saw.
    """
    repo = Path(repo)
    paths = input_paths(repo)
    if not paths:
        return {}
    for name in ("include_asm.h", "compiler_diagnostics.h"):
        paths.setdefault(f"public:{name}", repo / "include" / name)
    paths.setdefault("m2c:executable", repo / ".venv" / "bin" / "m2c")
    result = {}
    if not any(label.startswith("elf:") for label in paths):
        result["elf:<missing>"] = "missing-file"
    if not any(label.startswith("public:PR/") for label in paths):
        result["public:PR/<missing>"] = "missing-file"
    if not any(label.startswith("m2c-python:") for label in paths):
        result["m2c-python:<missing>"] = "missing-file"
    for label, path in sorted(paths.items()):
        if not path.is_file():
            result[label] = "missing-file"
        elif pins is not None:
            result[label] = pins.get(str(path.resolve()), "missing-pin")
        else:
            result[label] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _revision_from_hashes(hashes: dict[str, str]) -> str:
    base = code_digest()
    if not hashes:  # Small synthetic repos have no external draft inputs.
        return base
    payload = json.dumps({"code": base, "inputs": hashes}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def input_revision(repo: Path, pins: dict[str, str] | None = None) -> str:
    """Version the draft by generator code and binary/public/tool inputs."""
    return _revision_from_hashes(input_hashes(repo, pins))


def _public_dependencies(repo: Path, paths) -> list[dict]:
    # Worker isolation deliberately links the public include tree. Resolve its
    # root as well as each dependency, while retaining the narrow allow-list.
    root = (repo / "include").resolve()
    result = []
    for path in paths:
        path = path.resolve()
        if path.is_relative_to(root):
            rel = "include/" + path.relative_to(root).as_posix()
            if not (rel.startswith("include/PR/") or rel in ("include/include_asm.h", "include/compiler_diagnostics.h")):
                raise Unavailable("context dependency is not a public SDK header: " + rel)
            label = rel
        elif path.as_posix().startswith(("/usr/include/", "/usr/lib/gcc/")):
            label = str(path)
        else:
            raise Unavailable("context dependency is outside public header roots: " + str(path))
        if path.suffix != ".h":
            raise Unavailable("non-header public context dependency: " + str(path))
        result.append({"path": label, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def _preprocess(repo: Path, wrapper: str, scratch: Path) -> tuple[str, dict]:
    path = scratch / "declarations.c"
    path.write_text(wrapper)
    def cpp(*args):
        proc = subprocess.run(["gcc", "-E", *args], cwd=repo, capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise Unavailable("public preprocessing failed: " + proc.stderr[-1200:])
        return proc.stdout
    deps = cpp("-M", *CPP_FLAGS, str(path))
    tokens = shlex.split(deps.replace("\\\n", " "))[1:]
    paths = [Path(x) if Path(x).is_absolute() else repo / x for x in tokens]
    headers = _public_dependencies(repo, [p for p in paths if p.resolve() != path.resolve()])
    empty = scratch / "empty.c"
    empty.write_text("")
    stock = cpp("-P", "-dM", str(empty)) + "#define __STDC_HOSTED__ 0\n"
    text = cpp("-P", "-dM", *CPP_FLAGS, str(path)) + cpp("-P", *CPP_FLAGS, str(path))
    for line in stock.strip().splitlines():
        text = text.replace(line + "\n", "")
    return text, {"public_headers": headers, "cpp_flags": list(CPP_FLAGS),
                  "wrapper_sha256": hashlib.sha256(wrapper.encode()).hexdigest(),
                  "preprocessed_sha256": hashlib.sha256(text.encode()).hexdigest()}


def _m2c(repo: Path, assembly: str, context: str, scratch: Path, *, valid_syntax: bool):
    target = scratch / "target.s"
    target.write_text(assembly)
    ctx = scratch / "context.c"
    ctx.write_text(context)
    command = [str(repo / ".venv/bin/m2c"), "--target", "mips-ido-c", "--no-cache", "--context", str(ctx)]
    if valid_syntax:
        command.append("--valid-syntax")
    return subprocess.run([*command, str(target)], cwd=repo, capture_output=True, text=True, timeout=180)


def _strides(source: str) -> dict[str, int]:
    result = {}
    for name, value in re.findall(r"&(\w+) \+ \([^()]*\* (0x[0-9A-Fa-f]+|\d+)\)\)", source):
        result[name] = int(value, 0)
    for value, name in re.findall(r"\([^()]*\* (0x[0-9A-Fa-f]+|\d+)\) \+ &?(\w+)\b", source):
        result.setdefault(name, int(value, 0))
    for name, value in re.findall(r"\b&?(\w+) \+ \([^()]*\* (0x[0-9A-Fa-f]+|\d+)\)", source):
        result.setdefault(name, int(value, 0))
    return {k: v for k, v in result.items() if not k.startswith(("temp_", "var_", "arg", "sp")) and 0 < v <= 0x10000}


def variants(repo: Path, function: str, ws: Path, *, pointer_returns: bool = False,
             fixed_return: bool = False):
    reports, candidates, seen = [], [], set()
    hashes = input_hashes(repo)
    base = {"stage": "binary-type-draft", "reference_source_used": False,
            "assistance_tier": "source-independent", "policy": "D32",
            "input_hashes": hashes, "input_revision": _revision_from_hashes(hashes)}
    try:
        elf = binary_type_context.find_elf(repo)
        model = binary_type_context.load(elf)
        original = (ws / "target.s").read_text()
        assembly, aliases = m2c_input.normalize_o32_registers(original)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return [], [{**base, "status": "declined", "reason": str(exc)}]
    from solver import m2c_byte_view, repair_context
    # Two syntax spellings, each with at most one binary-grounded stride redraft.
    for valid_syntax in (False, True):
        strides = None
        for index in range(2):
            label = f"binary-types:{'valid' if valid_syntax else 'ordinary'}:{index + 1}"
            report = {**base, "label": label, "valid_syntax": valid_syntax, "o32_register_aliases": aliases}
            try:
                ctx = model.context(function, original, strides)
                report["evidence"] = ctx["evidence"]
                wrapper = CLEAN_PRELUDE + ctx["declarations"]
                with tempfile.TemporaryDirectory(prefix=".binary-draft-", dir=ws) as directory:
                    scratch = Path(directory)
                    preprocessed, meta = _preprocess(repo, wrapper, scratch)
                    report["preprocessing"] = meta
                    result = _m2c(repo, assembly, preprocessed, scratch, valid_syntax=valid_syntax)
                report["m2c_returncode"] = result.returncode
                if result.returncode:
                    raise Unavailable("m2c failed: " + (result.stderr or result.stdout)[-1200:])
                text = result.stdout
                if valid_syntax:
                    lowered = m2c_byte_view.lower(text, function, target_assembly=original)
                    text = lowered["source"] if isinstance(lowered, dict) else lowered
                definition, end = repair_context.definition(text, function)
                body = text[definition.start():end]
                # The provisional return/parameter hypothesis guided m2c. The
                # generated definition is the compiler hypothesis; don't declare
                # a conflicting second signature for it in the compile header.
                header = ctx["declarations"].replace(ctx["own_prototype"] + "\n", "", 1)
                source = CLEAN_PRELUDE + header + "\n" + body + "\n"
                report.update(status="generated", source_sha256=hashlib.sha256(source.encode()).hexdigest())
                if source not in seen:
                    candidates.append((label, source))
                    seen.add(source)
                else:
                    report["status"] = "duplicate"
                next_strides = {k: v for k, v in _strides(body).items() if k in model.symbols}
                reports.append(report)
                if not next_strides or next_strides == strides:
                    break
                strides = next_strides
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                reports.append({**report, "status": "declined", "reason": str(exc)})
                break
    if pointer_returns:
        from solver import m2c_pointer_return
        try:
            ctx = model.context(function, original)
            proposal = m2c_pointer_return.propose(function, original, ctx, fixed_return=fixed_return)
            detail = {k:v for k,v in proposal.items() if k != 'context'}
            if proposal['status'] != 'proposed':
                reports.append({**base, 'stage':'pointer-return', 'status':'declined', 'pointer_return':detail})
            else:
                ctx = proposal['context']
                for valid_syntax in (False, True):
                    label = f"binary-types:pointer-return:{'valid' if valid_syntax else 'ordinary'}"
                    report = {**base, 'label':label, 'pointer_return':detail, 'evidence':ctx['evidence']}
                    with tempfile.TemporaryDirectory(prefix='.pointer-return-', dir=ws) as directory:
                        scratch = Path(directory)
                        preprocessed, meta = _preprocess(repo, CLEAN_PRELUDE+ctx['declarations'], scratch)
                        report['preprocessing'] = meta
                        # The generated own prototype is provisional; another
                        # public declaration must not be rewritten with it.
                        from solver import project_headers
                        declarations = project_headers._mask_noncode(preprocessed)
                        if len(re.findall(r'\b'+re.escape(function)+r'\s*\(',declarations)) != 1:
                            reports.append({**report, 'status':'declined',
                                'reason':'public context has another declaration for the function'})
                            continue
                        result = _m2c(repo, assembly, preprocessed, scratch, valid_syntax=valid_syntax)
                    report['m2c_returncode'] = result.returncode
                    if result.returncode:
                        reports.append({**report, 'status':'declined', 'reason':(result.stderr or result.stdout)[-1200:]})
                        continue
                    text = result.stdout
                    if valid_syntax:
                        lowered = m2c_byte_view.lower(text, function, target_assembly=original)
                        text = lowered['source'] if isinstance(lowered,dict) else lowered
                    definition,end = repair_context.definition(text,function)
                    header = ctx['declarations'].replace(ctx['own_prototype']+'\n','',1)
                    source = CLEAN_PRELUDE+header+'\n'+text[definition.start():end]+'\n'
                    report.update(status='duplicate' if source in seen else 'generated',
                                  source_sha256=hashlib.sha256(source.encode()).hexdigest())
                    if source not in seen:
                        candidates.append((label,source)); seen.add(source)
                    reports.append(report)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            reports.append({**base, 'stage':'pointer-return', 'status':'declined', 'reason':str(exc)})
    return candidates, reports

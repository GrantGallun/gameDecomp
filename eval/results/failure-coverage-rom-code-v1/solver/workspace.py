"""Per-function matching workspace: the Oracle for a single function.

Wraps the host repo's own tooling (`tools/claude --bootstrap-only` and
`build.sh`) rather than reimplementing it. That tooling is mature, already
enforces the project's rules, and reports normalized assembly similarity.
An independent section/relocation certificate gates exactness; whole-ROM
verification remains a separate integration step.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from kb import attempts as attempt_receipts
from solver import byte_certificate

SCORE_RE = re.compile(r"^Score:\s*([\d.]+)%", re.MULTILINE)
EXACT_RE = re.compile(r"^Verified exact match:\s*(\w+)", re.MULTILINE)
ERROR_LINE_RE = re.compile(r"^(?:cfe: Error|ERROR|.*Syntax Error).*$", re.MULTILINE)
_RELOCATION_SYMBOL = re.compile(r"%(?:hi|lo)\(([^)+-]+)")
_LINKER_SYMBOL = re.compile(
    r"(?m)^([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;")


@dataclass
class Attempt:
    compiled: bool
    score: float          # 0..100 similarity; exact is authoritative
    exact: bool
    diff: str             # instruction diff, empty when it did not compile
    compiler_stderr: str
    raw_output: str
    receipt_id: int | None = None
    verification: dict | None = None
    compiler_recipe: dict | None = None
    frontend: dict | None = None


def repair_complete(attempt: Attempt) -> bool:
    """Byte equality is distinct from acceptance by the project's C policy."""
    return attempt.exact and (attempt.frontend is None or attempt.frontend.get("passed") is True)


def sh(cmd: str, cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


@contextlib.contextmanager
def _workspace_lock(ws: Path):
    """Serialize write-source / build / read-diff against other harnesses.

    build.sh takes its own flock, but only around the BUILD. The filenames are
    shared -- every caller scoring function F writes `F.c` and then reads
    `F_diff` -- so two harnesses interleave as:

        A writes F.c (its source)     B writes F.c (overwrites with its own)
        A builds  [build.sh's lock]   B builds  [build.sh's lock]
        A reads F_diff  <-- this is B's residual, against A's source

    Nothing errors and nothing looks wrong; A simply reports a diff belonging
    to someone else's candidate. This was caught by three runs of one fixed
    source returning three different proposal sets while an eval.compose_regress
    was running in another shell -- the score moved 91.909 / 91.974 and the
    register-fault count moved 38 / 39 for a source that never changed.

    A DIFFERENT lock file from build.sh's `.build.lock` on purpose: holding
    that one here would deadlock, since the build.sh we invoke would then block
    forever waiting for its parent.

    Degrades to a no-op where flock is unavailable, which keeps this importable
    on Windows -- the builds themselves only ever run under WSL.
    """
    lock = ws / ".score.lock"
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def bootstrap(repo: Path, func: str) -> Path:
    if not re.fullmatch(r"[A-Za-z_]\w*", func):
        raise ValueError("invalid function identifier")
    ws = repo / "nonmatchings" / func
    if not (ws / "target.s").exists():
        _, out = sh(f". .venv/bin/activate && ./tools/claude --bootstrap-only {func}",
                    cwd=repo, timeout=900)
        if not (ws / "target.s").exists():
            from solver.target_intake import bootstrap_resolved
            try:
                return bootstrap_resolved(repo, func)
            except RuntimeError as exc:
                raise type(exc)(f"{exc}\nOriginal bootstrap: {out[-400:]}") from exc
    return ws


def target_asm(ws: Path, func: str) -> str:
    """Just the function body; target.s carries a macro preamble first."""
    text = (ws / "target.s").read_text(errors="replace")
    start = text.find(f"glabel {func}")
    if start == -1:
        raise RuntimeError(f"glabel {func} not found in target.s")
    end = text.find(f"endlabel {func}")
    return text[start:end if end != -1 else None].strip()


def _elf_jump_words(path: Path) -> dict[str, list[tuple[int, int]]]:
    """Extract R_MIPS_32-to-.text words from ELF32 data sections.

    IDO emits switch tables as word addends plus relocations against ``.text``.
    The words are function-relative byte offsets, exactly what the differential
    runner needs to turn an indirect ``jr`` into an instruction index.  This
    intentionally ignores unrelocated constants and every non-text relocation.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] != 1:
        return {}
    endian = ">" if data[5] == 2 else "<" if data[5] == 1 else ""
    if not endian:
        return {}
    try:
        shoff = struct.unpack_from(endian + "I", data, 32)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(
            endian + "HHH", data, 46)
        if shentsize < 40 or shnum == 0:
            return {}
        sections = [struct.unpack_from(endian + "IIIIIIIIII", data,
                                       shoff + index * shentsize)
                    for index in range(shnum)]
        shstr = sections[shstrndx]
        names_blob = data[shstr[4]:shstr[4] + shstr[5]]

        def string(blob: bytes, offset: int) -> str:
            end = blob.find(b"\0", offset)
            if offset < 0 or offset >= len(blob):
                return ""
            return blob[offset:end if end >= 0 else None].decode(
                errors="replace")

        section_names = [string(names_blob, row[0]) for row in sections]
        symbols_by_table: dict[int, list[tuple[str, int, int, int]]] = {}
        for section_index, row in enumerate(sections):
            if row[1] != 2 or row[9] < 16:       # SHT_SYMTAB
                continue
            strings = sections[row[6]]
            strings_blob = data[strings[4]:strings[4] + strings[5]]
            symbols = []
            for at in range(row[4], row[4] + row[5], row[9]):
                name_at, value, size = struct.unpack_from(
                    endian + "III", data, at)
                info = data[at + 12]
                defined_in = struct.unpack_from(endian + "H", data, at + 14)[0]
                name = string(strings_blob, name_at)
                if (info & 0xF) == 3 and defined_in < len(section_names):
                    name = section_names[defined_in]
                symbols.append((name, value, size, defined_in))
            symbols_by_table[section_index] = symbols

        found: dict[str, list[tuple[int, int]]] = {}
        for row in sections:
            if row[1] != 9 or row[9] < 8:        # SHT_REL
                continue
            target_section = row[7]
            symbols = symbols_by_table.get(row[6], [])
            if target_section >= len(sections):
                continue
            target = sections[target_section]
            defined = [item for item in symbols if item[3] == target_section]
            for at in range(row[4], row[4] + row[5], row[9]):
                offset, info = struct.unpack_from(endian + "II", data, at)
                symbol_index, relocation_type = info >> 8, info & 0xFF
                if relocation_type != 2 or symbol_index >= len(symbols):
                    continue                          # not R_MIPS_32
                relocation_symbol = symbols[symbol_index]
                if (relocation_symbol[3] >= len(section_names) or
                        section_names[relocation_symbol[3]] != ".text"):
                    continue
                if offset + 4 > target[5]:
                    continue
                addend = struct.unpack_from(
                    endian + "I", data, target[4] + offset)[0]
                owners = [item for item in defined if item[1] <= offset and
                          (item[2] == 0 or offset < item[1] + item[2])]
                if not owners:
                    section_name = section_names[target_section]
                    owners = [(section_name, 0, target[5], target_section)]
                nearest = max(item[1] for item in owners)
                # A section symbol and a named table commonly share the same
                # start. Retain both aliases; semantic_assembly will keep only
                # whichever spelling the function's relocation uses.
                for owner in owners:
                    if owner[1] == nearest:
                        found.setdefault(owner[0], []).append(
                            (offset - owner[1], addend))
        return found
    except (IndexError, struct.error, ValueError):
        return {}


def _linker_symbol_addresses(object_path: Path) -> dict[str, int]:
    """Read mechanically recovered linker identities surrounding an object.

    Adjacent linker symbols can also be addressed as ``base + offset`` in C.
    Synthetic per-symbol regions destroy that aliasing and fabricate semantic
    mismatches.  ``symbol_addrs.txt`` is binary/linker evidence, not reference
    function source, so it is safe to attach to both target and candidate.
    """
    symbol_file = next(
        (parent / "symbol_addrs.txt" for parent in object_path.parents
         if (parent / "symbol_addrs.txt").is_file()), None)
    if symbol_file is None:
        return {}
    try:
        text = symbol_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {name: int(address, 16)
            for name, address in _LINKER_SYMBOL.findall(text)}


def _elf_data_symbols(path: Path) -> dict[str, bytes]:
    """Extract bounded initialized object/section bytes, never unresolved relocations."""
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < 52 or data[:4] != b"\x7fELF" or data[4] != 1:
        return {}
    endian = ">" if data[5] == 2 else "<" if data[5] == 1 else ""
    if not endian:
        return {}
    try:
        shoff = struct.unpack_from(endian + "I", data, 32)[0]
        shentsize, shnum, shstrndx = struct.unpack_from(
            endian + "HHH", data, 46)
        if shentsize < 40 or shnum == 0:
            return {}
        sections = [struct.unpack_from(endian + "IIIIIIIIII", data,
                                       shoff + index * shentsize)
                    for index in range(shnum)]
        shstr = sections[shstrndx]
        names_blob = data[shstr[4]:shstr[4] + shstr[5]]

        def string(blob: bytes, offset: int) -> str:
            if offset < 0 or offset >= len(blob):
                return ""
            end = blob.find(b"\0", offset)
            return blob[offset:end if end >= 0 else None].decode(
                errors="replace")

        section_names = [string(names_blob, row[0]) for row in sections]
        objects: list[tuple[str, int, int, int]] = []
        for row in sections:
            if row[1] != 2 or row[9] < 16:       # SHT_SYMTAB
                continue
            strings = sections[row[6]]
            strings_blob = data[strings[4]:strings[4] + strings[5]]
            for at in range(row[4], row[4] + row[5], row[9]):
                name_at, value, size = struct.unpack_from(
                    endian + "III", data, at)
                info = data[at + 12]
                defined_in = struct.unpack_from(endian + "H", data, at + 14)[0]
                name = string(strings_blob, name_at)
                kind = info & 0xF
                if kind not in (1, 3):  # STT_OBJECT or STT_SECTION
                    continue
                if defined_in == 0 or defined_in >= len(sections):
                    continue
                section = sections[defined_in]
                if section[1] != 1 or not section[2] & 2 or section[2] & 4:
                    continue  # initialized allocated non-code only
                if any(r[1] in (4, 9) and r[7] == defined_in and r[5] for r in sections):
                    continue  # raw relocation placeholders are not runtime data
                if kind == 3:
                    name = section_names[defined_in]
                    value, size = 0, section[5]
                if not name:
                    continue
                objects.append((name, value, size, defined_in))

        found: dict[str, bytes] = {}
        for name, value, size, section_index in objects:
            section = sections[section_index]
            section_size = section[5]
            if value >= section_size:
                continue
            if size:
                extent = size
            else:
                following = [other_value for _other_name, other_value,
                             _other_size, other_section in objects
                             if other_section == section_index and
                             other_value > value]
                extent = (min(following) if following else section_size) - value
            extent = min(extent, section_size - value, 256)
            if extent <= 0:
                continue
            start = section[4] + value
            found[name] = data[start:start + extent]
        return found
    except (IndexError, struct.error, ValueError):
        return {}


def semantic_assembly(assembly: str, object_path: Path) -> str:
    """Attach binary data/layout facts as comments consumed by the runner."""
    referenced = set(_RELOCATION_SYMBOL.findall(assembly))
    annotations = []
    linker_addresses = _linker_symbol_addresses(object_path)
    for symbol in sorted(referenced):
        if symbol in linker_addresses:
            annotations.append(
                f"# MIPS_DIFF_SYMBOL {symbol} {linker_addresses[symbol]:#x}")
    for symbol, words in sorted(_elf_jump_words(object_path).items()):
        if symbol not in referenced:
            continue
        for offset, text_offset in sorted(words):
            annotations.append(
                f"# MIPS_DIFF_DATA {symbol} {offset:#x} {text_offset:#x}")
    for symbol, payload in sorted(_elf_data_symbols(object_path).items()):
        if symbol in referenced and payload:
            annotations.append(
                f"# MIPS_DIFF_BYTES {symbol} {payload.hex()}")
    return (assembly.rstrip() + "\n" + "\n".join(annotations) + "\n"
            if annotations else assembly)


def m2c_draft(ws: Path) -> str:
    path = ws / "base.c"
    source = path.read_text(errors="replace") if path.exists() else ""
    repo = ws.parent.parent
    if (not source.strip() or "Decompilation failure:" in source) and (
            (repo / ".venv/bin/m2c").is_file() and (ws / "target.s").is_file()):
        from solver import m2c_input
        result, _ = m2c_input.draft(repo, ws / "target.s")
        if result.returncode == 0:
            return '#include "common.h"\n\n' + result.stdout
    return source


def _project_c_defines(repo: Path) -> tuple[tuple[str, str], ...]:
    """Read the target build's global ``C_DEFINES`` make variable.

    The isolated compiler harness already supplies a small historical subset,
    but project-wide feature macros such as ``F3DEX_GBI`` materially change
    SDK display-list encodings.  The Makefile is build metadata, not target C
    source, so carrying these declarations into candidate compilation keeps
    the oracle honest without leaking an implementation.
    """
    try:
        lines = (repo / "Makefile").read_text(errors="replace").splitlines()
    except OSError:
        return ()
    chunks: list[str] = []
    collecting = False
    for line in lines:
        if not collecting:
            match = re.match(r"^\s*C_DEFINES\s*(?::|\+|\?)?=\s*(.*)$", line)
            if match is None:
                continue
            text = match.group(1)
            collecting = True
        else:
            text = line.strip()
        continued = text.rstrip().endswith("\\")
        chunks.append(text.rstrip().removesuffix("\\"))
        if not continued:
            break
    if not chunks:
        return ()
    found = []
    for name, value in re.findall(
            r"(?:^|\s)-D([A-Za-z_]\w*)(?:=([^\s]+))?",
            " ".join(chunks)):
        # Quoted string values need C escaping and are not currently present
        # in C_DEFINES.  Ignore them rather than changing their spelling.
        if value.startswith(('"', "'")):
            continue
        found.append((name, value or "1"))
    return tuple(dict.fromkeys(found))


def _candidate_compile_source(repo: Path, code: str) -> str:
    defines = _project_c_defines(repo)
    if not defines:
        return code
    existing = set(re.findall(
        r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\b", code))
    lines = ["/* project C_DEFINES mirrored from Makefile */"]
    for name, value in defines:
        if name in existing:
            continue
        lines.extend((f"#ifndef {name}", f"#define {name} {value}", "#endif"))
    if len(lines) == 1:
        return code
    # Keep compiler diagnostics aligned with the exact unwrapped source shown
    # to the repair model.  The mirrored defines are build context and should
    # not add a hidden line-number offset to later compiler-fix gradients.
    lines.append('#line 1 "candidate.c"')
    return "\n".join(lines) + "\n" + code


def assert_uncontaminated(prompt: str, repo: Path, func: str) -> None:
    """Fail loudly if ground-truth source leaked into the prompt.

    The workspace's generated ctx.c contains the preprocessed translation unit
    INCLUDING the target function's own body. On an already-matched function
    that is the answer, and using it would fabricate a 100% that silently
    invalidates every number afterwards. Checked at runtime, not asserted in a
    comment.
    """
    from solver import project_headers
    # A public declaration can also occur in a reference TU. Its independent
    # included-header origin is permitted context, not implementation leakage.
    # Never exempt initializers, arbitrary extern text, or matching statements.
    def prototype_key(text):
        if not re.fullmatch(r'(?:extern\s+)?[\w\s*]+\s+\**[A-Za-z_]\w*\s*\([^;{}=]*\)\s*;', text):
            return None
        if project_headers._declared_name(text) is None:
            return None
        return re.sub(r'\s+', '', re.sub(r'^extern\s+', '', text.strip()))
    header_keys = {key for declarations in project_headers._included_declarations(repo,prompt).values()
                   for declaration in declarations if (key := prototype_key(declaration)) is not None}
    _, out = sh(f"grep -rn --include=*.c -A 8 '^[a-zA-Z_].*{func}(' src/ | head -20",
                cwd=repo)
    for line in out.splitlines():
        body = line.split(":", 2)[-1].strip()
        if len(body) > 25 and body.endswith(";") and body in prompt:
            if prototype_key(body) in header_keys:
                continue
            raise RuntimeError(
                f"CONTAMINATION: ground-truth line leaked into prompt for {func}:\n  {body}")


def record_attempt(conn, func: str, code: str, att: "Attempt", *,
                   strategy: str = "adhoc", model: str = "", prompt: str = "",
                   temperature=None, wall_ms: int = 0, run_id: str = "",
                   token_cost: int = 0, iteration: int = 0,
                   parent_attempt_id: int | None = None,
                   relation: str = "", action: str = "", feedback: str = "",
                   run_kind: str = "", run_config: dict | None = None,
                   extra: dict | None = None, raw_response: str = "",
                   extract_status: str = "", done_reason: str = ""
                   ) -> int | None:
    """Record one attempt and return its durable receipt id.

    CLAUDE.md requires every attempt to be logged, including failures -- it is
    the debugging record now and the training set later. Only refine.py ever
    did, so every ad-hoc experiment harness wrote ZERO rows: an entire day of
    runs, ~250 generations, left no trace and cannot be recovered.

    Parentage is explicit. Consecutive rows are not necessarily a trajectory:
    best-anchored refinement can branch from an older row, and best-of-N draws
    have no attempt parent at all.
    """
    if conn is None:
        return None
    row = conn.execute("select addr from functions where name=?",
                       (func,)).fetchone()
    if not row:
        return None
    attempt_receipts.ensure_lineage_schema(conn)
    sampling = {"temperature": temperature, "run_id": run_id}
    if extra:
        sampling.update(extra)
    if att.verification is not None:
        sampling["verification"] = att.verification
    attempt_receipts.start_run(
        conn, run_id, kind=run_kind or strategy, model=model,
        config=run_config or sampling, ensure_schema=False)

    source_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    prompt_hash = (hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                   if prompt else None)
    cur = conn.execute(
        "INSERT INTO attempts ("
        "func_addr, iteration, run_id, parent_attempt_id, source_code,"
        " source_sha256, prompt_context, prompt_sha256, compiled,"
        " compiler_stderr, score, exact, diff_summary, strategy, model,"
        " sampling, wall_ms, token_cost, raw_response, extract_status,"
        " done_reason, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row[0], int(iteration), run_id or None, parent_attempt_id, code,
         source_hash, prompt, prompt_hash, int(att.compiled),
         att.compiler_stderr, att.score, int(att.exact), att.diff, strategy,
         model, json.dumps(sampling, sort_keys=True), wall_ms,
         max(0, int(token_cost)), raw_response, extract_status, done_reason,
         int(time.time())))
    receipt_id = int(cur.lastrowid)
    att.receipt_id = receipt_id
    if parent_attempt_id is not None:
        conn.execute(
            "INSERT INTO attempt_edges (parent_attempt_id, child_attempt_id,"
            " relation, action, feedback, created_at) VALUES (?,?,?,?,?,?)",
            (parent_attempt_id, receipt_id, relation or "derive", action,
             feedback, int(time.time())))
    conn.commit()
    return receipt_id


def log_attempt(conn, func: str, code: str, att: "Attempt", **kwargs) -> bool:
    """Backward-compatible boolean wrapper around :func:`record_attempt`."""
    return record_attempt(conn, func, code, att, **kwargs) is not None


def configure_compiler(ws: Path, repo: Path, conn, func: str):
    """Establish a TU recipe before an unlogged search baseline is compiled."""
    from solver import compiler_recipe
    with _workspace_lock(ws):
        return compiler_recipe.prepare(repo, ws, conn, func)


def score(ws: Path, repo: Path, name: str, code: str, conn=None,
          func: str = "", **log_kw) -> Attempt:
    """Compile one candidate and score it against the target object.

    Pass `conn` and `func` to log the attempt automatically. Optional so no
    existing caller breaks, but every new harness should pass them -- see
    log_attempt for why.
    """
    with _workspace_lock(ws):
        from solver import compiler_recipe
        prepared = compiler_recipe.prepare(repo, ws, conn, func)
        build_script, recipe = prepared if prepared else (ws / "build.sh", None)
        compile_source = _candidate_compile_source(repo, code)
        (ws / f"{name}.c").write_text(compile_source)
        import shlex
        returncode, out = sh(f". {shlex.quote(str(repo / '.venv/bin/activate'))} && bash {shlex.quote(str(build_script))} {shlex.quote(name + '.c')}",
                    cwd=ws, timeout=300)
        m = SCORE_RE.search(out) if returncode == 0 else None
        frontend = None
        if recipe and recipe.get("target") and "CC_CHECK" in (repo / "Makefile").read_text():
            from solver import frontend_check
            frontend = frontend_check.check(repo, ws / f"{name}.c", recipe["target"])
        verification = None
        if m:
            diff_path = ws / f"{name}_diff"
            diff_text = (diff_path.read_text(errors="replace")
                         if diff_path.exists() else "")
            # Read artifacts while holding the same lock as source/build/diff.
            exact_m = EXACT_RE.search(out)
            normalized_exact = bool(exact_m and exact_m.group(1) == "yes")
            if normalized_exact:
                build_inputs = {}
                paths = [ws / "build.sh", ws / "prelude.inc", repo / "Makefile",
                         repo / "tools/textconv.py", repo / "tools/charmap.txt"]
                if recipe:
                    paths += [build_script, Path(recipe["manifest"]), ws / ".compiler-target.json"]
                    paths += list((repo / "src").rglob("*.h"))
                if frontend and frontend.get("recipe"):
                    paths.append(Path(frontend["recipe"]["command"][0]))
                if (repo / "include").is_dir():
                    paths += sorted((repo / "include").rglob("*.h"))
                compiler_dir = repo / "tools/ido-recomp/linux"
                if compiler_dir.is_dir():
                    paths += sorted(p for p in compiler_dir.iterdir() if p.is_file())
                for path in paths:
                    if path.is_file():
                        build_inputs[str(path)] = byte_certificate.digest(path.read_bytes())
                verification = byte_certificate.certify(
                    ws / "target.o", ws / f"{name}.o", source=compile_source,
                    build_inputs=build_inputs)
                verification["normalized_assembly_exact"] = True
                verification["build_manifest_scope"] = (
                    "build script, prelude, Makefile, project headers, text conversion inputs, IDO/checker binaries; "
                    "not a hermetic dependency or final-link certificate")
                verification["candidate_source_sha256"] = byte_certificate.digest(code.encode())
                verification["compiler_recipe"] = recipe
                verification["frontend"] = frontend
                if not verification["exact"] and conn is not None and func:
                    from solver import function_boundary
                    metadata = conn.execute("SELECT addr,size FROM functions WHERE name=?", (func,)).fetchone()
                    if metadata:
                        verification["function_boundary"] = function_boundary.certify(
                            target=ws / "target.o", candidate=ws / f"{name}.o", assembly=ws / "target.s",
                            rom=repo / "snowboardkids.z64", config=repo / "snowboardkids.yaml",
                            symbols=repo / "symbol_addrs.txt", function=func, address=metadata[0], size=metadata[1])
                (ws / f"{name}.verification.json").write_text(
                    json.dumps(verification, indent=2), encoding="utf-8")

    if not m:
        errors = "\n".join(ERROR_LINE_RE.findall(out)[:6])
        att = Attempt(False, 0.0, False, "", errors or out[-700:], out)
    else:
        exact = bool(verification and verification["exact"])
        att = Attempt(True, float(m.group(1)), exact, diff_text, "", out,
                      verification=verification)

    # Failures are logged too: the non-compiling rows are exactly what made
    # today's extraction bugs findable.
    att.compiler_recipe = recipe
    att.frontend = frontend
    if frontend:
        log_kw["extra"] = {**(log_kw.get("extra") or {}), "frontend": frontend}
    if recipe:
        log_kw["extra"] = {**(log_kw.get("extra") or {}), "compiler_recipe": recipe}
    if conn is not None and func:
        record_attempt(conn, func, code, att, **log_kw)
    return att

"""Error-focused model prompt for candidates that do not compile (2026-09-15).

Measured on the live campaign (eval/results/ninety-census-20260914/model_failure_modes.py,
prompt_sections.py): 259 of 494 model jobs on still-stuck non-compiling nodes never reached the
model (ContextBudgetError; gpt-oss:20b has 32,768 tokens and 6,000 are reserved for output). The
repair prompt carried read-only byte-repair context that is empty or unused before anything compiles
(storage/type packet median 18.8 KB, target assembly 13 KB, residual packet 5 KB, the source twice),
while the compiler error itself was a 210-byte list of bare line numbers.

This prompt leads with every IDO and clang error, each beside its numbered source line, then the
editable source once as slots, then optional evidence packed in priority order until the byte budget
is used. It keeps the ordinary proposal schema, so parsing, slot binding and validation are unchanged.
"""
from __future__ import annotations

import re

from solver import edit_slots

IDO_ERROR = re.compile(r"(?m)^cfe: (?P<level>Error|Warning): candidate\.c, line (?P<line>\d+): (?P<message>.*)$")
CLANG_ERROR = re.compile(r"(?m)^candidate\.c:(?P<line>\d+):(?P<column>\d+): error: (?P<message>.*)$")

PREAMBLE = """\
The C candidate below does NOT compile, so no object exists yet. Make it compile under the configured
IDO 5.3 C89 compiler and the project's clang frontend policy, keeping the function's behavior and the
target machine code it must eventually reproduce. Make ONE coordinated hypothesis that removes the errors.

Return JSON only, with this exact shape:
{{
  "kind": "one of: {kinds}",
  "hypothesis": "which errors this fixes and why",
  "edits": [{{"slot":"L23","new":"replacement for this complete line"}}]
}}

Rules:
- 1 to {max_edits} edits. Use a source slot ID (L<n> or DECLARATIONS) from EDITABLE SOURCE, OR an old
  substring that occurs exactly once in the source; never both in one edit.
- A line slot replaces that ONE complete line. Keep braces balanced; do not merge or split blocks.
- Fix the reported errors. IDO stops early, so later lines may hide more errors of the same kind: fix every
  occurrence of the same construct, not only the first line.
- C89 only: declarations at the start of a block, no `//` comments, no declarations inside `for (...)`.
- IDO rejects arithmetic on `void *` and `->field` through `void *` or an undefined struct: use a typed
  pointer or a byte view such as `(*(s32 *)((u8 *)p + 0x10))`, with the width the target reads or writes.
- m2c placeholders `?`, `M2C_UNK`, `M2C_FIELD(...)`, `(bitwise T)` are not C: replace them with real types.
- Do not add a declaration that an included header already provides (see HEADER DECLARATIONS); do not
  redeclare with a different type.
- Headers, assembly and evidence are READ-ONLY. No inline assembly, GLOBAL_ASM or reference source.
"""


def errors(compiler_stderr: str, diagnostics: str) -> dict[int, list[str]]:
    """line -> distinct messages, IDO first, then clang (with column)."""
    by_line: dict[int, list[str]] = {}
    for found in IDO_ERROR.finditer(compiler_stderr or ""):
        if found.group("level") == "Error":
            by_line.setdefault(int(found.group("line")), []).append("IDO: " + found.group("message").strip())
    for found in CLANG_ERROR.finditer(diagnostics or ""):
        message = f"clang col {found.group('column')}: " + found.group("message").strip()
        by_line.setdefault(int(found.group("line")), []).append(message)
    return {line: list(dict.fromkeys(messages)) for line, messages in by_line.items()}


def other_failure(compiler_stderr: str) -> str:
    """Non-diagnostic build failures (helper policy, missing symbols) that carry no line number."""
    lines = [l for l in (compiler_stderr or "").splitlines()
             if l.strip() and not IDO_ERROR.match(l) and not l.startswith("cfe: Warning")]
    return "\n".join(lines[-12:])


def error_block(source: str, by_line: dict[int, list[str]], *, context: int = 2, limit: int = 24) -> str:
    lines = source.splitlines()
    rows, windows = [], []
    for number in sorted(by_line)[:limit]:
        start, stop = max(1, number - context), min(len(lines), number + context)
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], stop), windows[-1][2] + [number])
        else:
            windows.append((start, stop, [number]))
    for start, stop, numbers in windows:
        for number in numbers:
            rows.append(f"-- line {number}:")
            rows += [f"   {message[:300]}" for message in by_line[number][:4]]
        for index in range(start, stop + 1):
            marker = ">>" if index in numbers else "  "
            rows.append(f"{marker} L{index}: {lines[index - 1][:220]}")
    if len(by_line) > limit:
        rows.append(f"... {len(by_line) - limit} more error lines of the kinds above")
    return "\n".join(rows)


def windowed_slots(source: str, keep_lines: set[int]) -> str:
    """Slot table restricted to kept physical lines (DECLARATIONS always kept); elisions marked."""
    table = edit_slots.render(source)
    last, out = 0, []
    for row in table.splitlines():
        found = re.match(r"^L(\d+): ", row)
        if not found:
            out.append(row)
            continue
        number = int(found.group(1))
        if number in keep_lines:
            if last and number > last + 1:
                out.append(f"   ... lines {last + 1}-{number - 1} omitted (still editable by slot) ...")
            out.append(row)
            last = number
    return "\n".join(out) + "\n"


LOCAL_DECLARATION = re.compile(r"^\s*(?:(?:register|volatile|const|unsigned|signed|struct|union|enum|static)\s+)*"
                               r"[A-Za-z_]\w*[\s*]+[A-Za-z_]\w*(?:\s*\[[^\]]*\])*\s*(?:=[^;]*)?;\s*(?:/\*.*\*/)?\s*$")


def declaration_lines(source: str, function: str) -> set[int]:
    """1-based lines of file-scope text before the definition, the signature, and the local declaration block."""
    from solver import repair_context
    lines = source.splitlines()
    try:
        match, _end = repair_context.definition(source, function)
    except ValueError:
        return set(range(1, min(len(lines), 40) + 1))
    body_line = source.count("\n", 0, match.end()) + 1
    keep = set(range(1, body_line + 1))
    for index in range(body_line + 1, len(lines) + 1):
        text = lines[index - 1]
        if text.strip() and not LOCAL_DECLARATION.match(text):
            break
        keep.add(index)
    return keep


def build(source: str, *, function: str, compiler_stderr: str, diagnostics: str, kinds, max_edits: int,
          header_declarations: str = "", history: tuple[str, ...] = (), rejected: list[str] | None = None,
          assembly: str = "", storage_packet: str = "", budget_bytes: int = 44000) -> tuple[str, dict]:
    """(prompt, report). Sections are packed in priority order until budget_bytes is reached."""
    by_line = errors(compiler_stderr, diagnostics)
    report = {"error_lines": len(by_line), "sections": [], "omitted": [], "source_windowed": False}
    parts = [PREAMBLE.format(kinds=", ".join(sorted(kinds)), max_edits=max_edits)]
    blocks = "COMPILER ERRORS WITH SOURCE LINES (fix these):\n" + (error_block(source, by_line) or "(no line-bound error)")
    extra = other_failure(compiler_stderr)
    if extra and not by_line:
        blocks += "\nBUILD FAILURE OUTPUT:\n" + extra
    parts.append(blocks + "\n")
    report["sections"].append("errors")
    # The slot table has no preprocessor lines. The include context is needed by the model, and by
    # workspace.assert_uncontaminated, which exempts a header-declared prototype only when the prompt
    # itself includes that header (osInitialize tripped it without these lines, 2026-09-15).
    includes = re.findall(r'(?m)^[ \t]*#\s*include\s*[<"][^>"]+[>"][^\n]*$', source)
    if includes:
        parts.append("INCLUDED HEADERS (read-only context of CURRENT C):\n" + "\n".join(i.strip() for i in includes) + "\n")
        report["sections"].append("includes")

    def size():
        return len("\n".join(parts).encode())

    full_slots = edit_slots.render(source)
    remaining = budget_bytes - size() - 2500            # reserve for history and the caller's small suffixes
    if len(full_slots.encode()) <= remaining * 0.6 or not by_line:
        parts.append("EDITABLE SOURCE (slot table; the line numbers are the compiler's line numbers):" + full_slots)
    else:
        lines = source.splitlines()
        keep = declaration_lines(source, function)
        radius = 8
        while True:
            window = set(keep)
            for number in by_line:
                window |= set(range(max(1, number - radius), min(len(lines), number + radius) + 1))
            table = windowed_slots(source, window)
            if len(table.encode()) <= remaining * 0.6 or radius <= 2:
                break
            radius //= 2
        parts.append("EDITABLE SOURCE (slot table, windowed around errors; omitted lines keep their slot IDs):" + table)
        report["source_windowed"] = True
    report["sections"].append("source")
    if header_declarations.strip():
        parts.append(header_declarations.strip() + "\n")
        report["sections"].append("header_declarations")
    notes = list(history[-4:]) + list((rejected or [])[-4:])
    if notes:
        parts.append("PREVIOUS OR REJECTED ATTEMPTS ON THIS SOURCE:\n- " + "\n- ".join(n[:400] for n in notes) + "\n")
        report["sections"].append("history")
    for title, text in (("TARGET ASSEMBLY (READ-ONLY; widths and offsets for byte views):", assembly),
                        ("STORAGE/TYPE RECONSTRUCTION INPUT (READ-ONLY):", storage_packet)):
        if not text.strip():
            continue
        block = f"{title}\n```\n{text.strip()}\n```\n"
        if size() + len(block.encode()) <= budget_bytes:
            parts.append(block)
            report["sections"].append(title.split(" (")[0].lower())
        else:
            report["omitted"].append(title.split(" (")[0].lower())
    prompt = "\n".join(parts)
    report["bytes"] = len(prompt.encode())
    return prompt, report

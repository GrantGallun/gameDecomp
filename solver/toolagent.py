"""Bounded observation/action repair agent driven by a local model.

Unlike the proposal-only adapter, the model may investigate before editing.
It never receives a shell: an allowlisted controller searches project headers,
reads bounded header ranges, renders focused verifier residuals, selects saved
candidates, and compiles validated substring patches.  The oracle owns success,
the controller owns writes and rollback, and every model action is receipted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kb import attempts as attempt_receipts
from solver import diff_annotate, llm, modelrepair, residual, signals, workspace


MAX_QUERY = 120
MAX_OBSERVATION = 6000
MAX_HEADER_LINES = 120
MAX_OPENBOOK_OBSERVATION = 16000
MAX_OPENBOOK_LINES = 400
MAX_REPLACEMENT_SOURCE = 100000
ACTIONS = {
    "inspect_definition", "read_header", "inspect_diff",
    "inspect_history", "select_candidate", "patch", "finish",
    "search_repo", "read_path", "replace_source",
    "inspect_evidence", "compiler_probe", "record_hypothesis",
    "inspect_compiler", "execution_case", "request_capability",
    "inspect_capabilities",
}
ACTION_ALIASES = {
    "inspect_header": "read_header",
    "read_file": "read_header",
}
INSPECTION_ACTIONS = {
    "inspect_definition", "read_header", "inspect_diff", "inspect_history",
    "search_repo", "read_path",
    "inspect_evidence", "compiler_probe",
    "inspect_compiler", "execution_case",
    "inspect_capabilities",
}
CONTEXT_ACTIONS = {"inspect_compiler", "execution_case", "request_capability"}
OPEN_BOOK_ACTIONS = {"search_repo", "read_path", "replace_source"}
DIFF_VIEWS = {"first", "full", "layout", "relocation", "register", "bytes"}
MEMORY_OP = re.compile(r"\b(?:lb|lbu|lh|lhu|lw|ld|sb|sh|sw|sd|lwc1|swc1)\b")
REGISTER = re.compile(
    r"\$?\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")


PROMPT = """\
You are controlling a bounded compiler-debugging investigation. The goal is C
that the authoritative verifier marks exact=true. The weighted score is only a
diagnostic and may temporarily regress.

Return exactly ONE JSON action and no prose. Available actions:
{{"action":"inspect_definition","query":"TypeOrSymbol"}}
{{"action":"read_header","path":"include/file.h","start":1,"end":80}}
{{"action":"inspect_diff","view":"first|full|layout|relocation|register|bytes"}}
{{"action":"inspect_history"}}
{{"action":"select_candidate","candidate_id":"c0"}}
{{"action":"patch","kind":"control-flow|type-width|layout|call-arguments|expression|declarations|temporaries|other","hypothesis":"one testable source-level claim","edits":[{{"old":"exact unique substring","new":"replacement"}}]}}
{{"action":"finish","reason":"why no useful bounded action remains"}}

Rules:
- You MUST execute at least one inspection action before the first patch. Use
  the returned evidence in a testable hypothesis; do not guess immediately.
- Investigate missing declarations or uncertain layouts instead of guessing.
- Header tools can read only the project's include tree. Target/reference C is
  inaccessible. Do not request shell commands or source files.
- A patch contains 1-4 bounded exact substring replacements against ACTIVE C.
- No inline assembly, includes, complete-file replacement, or comment-only edits.
- After a patch you will see its actual compiler/object residual. Select an
  earlier candidate when a child is a dead end.
- exact=true is the only success condition.

TARGET ASSEMBLY:
```
{asm}
```

ACTIVE CANDIDATE: {active_id}
```c
{source}
```

ACTIVE RESIDUAL:
```json
{packet}
```

DETERMINISTIC DIAGNOSIS:
{diagnosis}

SAVED CANDIDATES:
{candidates}

RECENT TOOL OBSERVATIONS AND PATCH OUTCOMES:
{history}
"""

OPEN_BOOK_PROMPT = """\
You are an autonomous compiler-debugging agent. Find C that the authoritative
verifier marks exact=true. There is no prescribed workflow: investigate,
experiment, rewrite, branch, or roll back as you judge useful. Weighted score
is diagnostic only.

Return exactly ONE JSON action per turn so the controller can execute it and
give you the result. You may use any of these actions:
{{"action":"search_repo","root":"both","query":"any text"}}
{{"action":"read_path","root":"target","path":"relative/path","start":1,"end":10000}}
{{"action":"read_path","root":"workbench","path":"relative/path","start":1,"end":10000}}
{{"action":"inspect_definition","query":"TypeOrSymbol"}}
{{"action":"read_header","path":"include/file.h","start":1,"end":10000}}
{{"action":"inspect_diff","view":"first|full|layout|relocation|register|bytes"}}
{{"action":"inspect_history"}}
{{"action":"select_candidate","candidate_id":"c0"}}
{{"action":"patch","kind":"control-flow|type-width|layout|call-arguments|expression|declarations|temporaries|other","hypothesis":"testable claim","edits":[{{"old":"exact unique substring","new":"replacement"}}]}}
{{"action":"replace_source","hypothesis":"why this complete rewrite should match","source":"complete candidate C"}}
{{"action":"finish","reason":"why no useful action remains"}}

You may search and read any project text in the target repository or workbench,
including sibling C, headers, build scripts, documentation, prior attempts, and
solver code. The sole evidence exclusion is the selected target function's
reference C definition, which is automatically redacted everywhere. Do not try
to evade that exclusion. Project compiler rules remain authoritative.

EXPERIMENT POLICY:
{policy}

RETRIEVED COMPILER PRINCIPLES:
{principles}

TARGET ASSEMBLY:
```
{asm}
```

ACTIVE CANDIDATE: {active_id}
```c
{source}
```

ACTIVE RESIDUAL:
```json
{packet}
```

DETERMINISTIC DIAGNOSIS:
{diagnosis}

SAVED CANDIDATES:
{candidates}

RECENT OBSERVATIONS AND COMPILE OUTCOMES:
{history}
"""


@dataclass(frozen=True)
class Action:
    name: str
    query: str = ""
    path: str = ""
    start: int = 1
    end: int = 80
    view: str = ""
    candidate_id: str = ""
    reason: str = ""
    root: str = ""
    source: str = ""
    hypothesis: str = ""
    proposal: modelrepair.Proposal | None = None
    alternatives: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict)


@dataclass
class Candidate:
    candidate_id: str
    source: str
    attempt: workspace.Attempt
    object_path: Path | None = None
    parent_id: str | None = None
    action: str = "root"
    semantic: dict | None = None


@dataclass
class Result:
    function: str
    root: Candidate
    best: Candidate
    exact: bool = False
    calls_attempted: int = 0
    generations: int = 0
    compiles: int = 0
    tokens: int = 0
    charged_tokens: int = 0
    tool_actions: int = 0
    invalid_actions: int = 0
    events: list[dict] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)


def _object(text: str) -> dict | None:
    return next(modelrepair._objects(text), None)


def parse_action(text: str) -> Action:
    value = _object(text)
    if value is None:
        raise ValueError("no JSON action")
    name = ACTION_ALIASES.get(value.get("action"), value.get("action"))
    if name not in ACTIONS:
        raise ValueError("unknown action")
    if name in CONTEXT_ACTIONS:
        hypothesis = value.get('hypothesis', '')
        if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 1000:
            raise ValueError('a bounded testable hypothesis is required')
        payload = value.get('inputs' if name == 'execution_case' else 'task', {})
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 16000:
            raise ValueError('invalid bounded action payload')
        return Action(name, hypothesis=hypothesis.strip(), payload=payload)
    if name == 'record_hypothesis':
        subject, alternatives, support = value.get('subject'), value.get('alternatives'), value.get('support')
        if not isinstance(subject, str) or not 1 <= len(subject) <= 120:
            raise ValueError('invalid hypothesis subject')
        if not isinstance(alternatives, list) or not 2 <= len(alternatives) <= 4 or any(
                not isinstance(a, str) or not 1 <= len(a) <= 1000 for a in alternatives):
            raise ValueError('hypothesis needs two to four bounded alternatives')
        if not isinstance(support, list) or not 1 <= len(support) <= 4 or any(
                not isinstance(s, str) or not re.fullmatch('[0-9a-f]{64}', s) for s in support):
            raise ValueError('hypothesis needs observation receipt IDs')
        return Action(name, query=subject, alternatives=tuple(alternatives), evidence_ids=tuple(support))
    if name in {"inspect_definition", "inspect_evidence", "inspect_capabilities"}:
        query = value.get("query")
        if not isinstance(query, str) or not query.strip() \
                or len(query) > MAX_QUERY:
            raise ValueError("invalid definition query")
        return Action(name, query=query.strip())
    if name == "read_header":
        path = value.get("path")
        start, end = value.get("start", 1), value.get("end", 80)
        if not isinstance(path, str) or not path.strip():
            raise ValueError("invalid header path")
        if not isinstance(start, int) or not isinstance(end, int) \
                or start < 1 or end < start:
            raise ValueError("invalid header line range")
        return Action(name, path=path.strip(), start=start, end=end)
    if name == "inspect_diff":
        view = value.get("view")
        if view not in DIFF_VIEWS:
            raise ValueError("invalid diff view")
        return Action(name, view=view)
    if name == "search_repo":
        root = value.get("root", "target")
        if root == "target|workbench":
            root = "both"
        query = value.get("query")
        if root not in {"target", "workbench", "both"}:
            raise ValueError("invalid search root")
        if not isinstance(query, str) or not query.strip() \
                or len(query) > MAX_QUERY:
            raise ValueError("invalid repository query")
        return Action(name, query=query.strip(), root=root)
    if name == "read_path":
        root = value.get("root", "target")
        path = value.get("path")
        start, end = value.get("start", 1), value.get("end", 300)
        if root not in {"target", "workbench"}:
            raise ValueError("invalid read root")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("invalid path")
        if not isinstance(start, int) or not isinstance(end, int) \
                or start < 1 or end < start:
            raise ValueError("invalid open-book line range")
        return Action(name, path=path.strip(), start=start, end=end, root=root)
    if name == "select_candidate":
        candidate_id = value.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("invalid candidate id")
        return Action(name, candidate_id=candidate_id)
    if name == "patch":
        kind = value.get("kind")
        if isinstance(kind, str) and kind not in modelrepair.KINDS:
            # ``kind`` is a diagnostic label, not an executable permission.
            # Preserve broad model vocabulary without rejecting valid edits.
            value = dict(value)
            value["kind"] = "other"
        proposal = modelrepair.parse_proposal(json.dumps(value))
        return Action(name, proposal=proposal)
    if name in {"replace_source", "compiler_probe"}:
        source = value.get("source")
        hypothesis = value.get("hypothesis", "")
        if not isinstance(source, str) or not source.strip() \
                or len(source) > MAX_REPLACEMENT_SOURCE:
            raise ValueError("invalid replacement source")
        if not isinstance(hypothesis, str) or not hypothesis.strip() \
                or len(hypothesis) > 1000:
            raise ValueError("invalid replacement hypothesis")
        return Action(name, source=source, hypothesis=hypothesis.strip())
    if name == "finish":
        reason = value.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise ValueError("invalid finish reason")
        return Action(name, reason=reason.strip())
    return Action(name)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def inspect_definition(repo: Path, query: str) -> str:
    """Search declarations in ``include/`` only; never inspect target C."""
    include = repo / "include"
    if not include.is_dir():
        return "include tree is unavailable"
    needle = query.casefold()
    rows: list[str] = []
    for path in sorted(include.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".h", ".inc"}:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines):
            if needle not in line.casefold():
                continue
            low, high = max(0, index - 2), min(len(lines), index + 3)
            relative = path.relative_to(repo).as_posix()
            rows.append(f"{relative}:{index + 1}\n" + "\n".join(
                f"{line_no + 1:5}: {lines[line_no]}"
                for line_no in range(low, high)))
            if len(rows) >= 10:
                return "\n\n".join(rows)[:MAX_OBSERVATION]
    return ("\n\n".join(rows)[:MAX_OBSERVATION]
            if rows else f"no include declaration matched {query!r}")


def read_header(repo: Path, path: str, start: int, end: int) -> str:
    include = (repo / "include").resolve()
    requested = (repo / path).resolve()
    if not _inside(requested, include) or requested.suffix.lower() not in {".h", ".inc"}:
        return "DENIED: only .h/.inc files under include/ may be read"
    if not requested.is_file():
        return "header does not exist"
    lines = requested.read_text(errors="replace").splitlines()
    start_index, end_index = start - 1, min(end, len(lines))
    return "\n".join(
        f"{index + 1:5}: {lines[index]}"
        for index in range(start_index, end_index))[:MAX_OBSERVATION]


TEXT_SUFFIXES = {
    ".c", ".h", ".inc", ".s", ".asm", ".py", ".sh", ".md", ".txt",
    ".json", ".jsonl", ".toml", ".yaml", ".yml", ".ini", ".cfg",
    ".mk", ".ld", ".map", ".diff", ".log",
}
DENIED_PARTS = {
    ".git", ".venv", ".pytest_cache", "__pycache__", "node_modules",
}
MAX_INDEXED_FILES = 5000
MAX_INDEXED_FILE_BYTES = 2_000_000
_SEARCH_INDEX: dict[Path, tuple[tuple[str, str, str], ...]] = {}


def clear_search_index() -> None:
    """Discard open-book text snapshots (primarily useful to tests/runners)."""
    _SEARCH_INDEX.clear()


def _search_index(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Read project text once instead of recursively scanning per model turn.

    The snapshot stores raw text. Target-function redaction remains query-time
    and therefore cannot be accidentally bypassed when one index serves
    several functions.
    """
    resolved = root.resolve()
    cached = _SEARCH_INDEX.get(resolved)
    if cached is not None:
        return cached
    rows: list[tuple[str, str, str]] = []
    for directory, directories, files in os.walk(resolved):
        directories[:] = sorted(
            item for item in directories
            if item not in DENIED_PARTS and not item.lower().startswith(".env"))
        base = Path(directory)
        for filename in sorted(files):
            path = base / filename
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > MAX_INDEXED_FILE_BYTES:
                    continue
                text = path.read_text(errors="replace")
            except OSError:
                continue
            rows.append((path.relative_to(resolved).as_posix(), text,
                         text.casefold()))
            if len(rows) >= MAX_INDEXED_FILES:
                result = tuple(rows)
                _SEARCH_INDEX[resolved] = result
                return result
    result = tuple(rows)
    _SEARCH_INDEX[resolved] = result
    return result


def _redact_target_definition(text: str, name: str) -> str:
    """Redact C definitions of ``name`` while leaving sibling source visible."""
    spans: list[tuple[int, int]] = []
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    for match in pattern.finditer(text):
        cursor = match.end() - 1
        depth = 0
        while cursor < len(text):
            char = text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= len(text):
            continue
        after = cursor + 1
        while after < len(text) and text[after].isspace():
            after += 1
        if after >= len(text) or text[after] != "{":
            continue
        brace = after
        depth = 0
        while brace < len(text):
            if text[brace] == "{":
                depth += 1
            elif text[brace] == "}":
                depth -= 1
                if depth == 0:
                    brace += 1
                    break
            brace += 1
        line_start = text.rfind("\n", 0, match.start()) + 1
        spans.append((line_start, min(brace, len(text))))
    for start, end in reversed(spans):
        line_count = text[start:end].count("\n")
        marker = f"/* DENIED: reference C definition of {name} */"
        text = text[:start] + marker + ("\n" * line_count) + text[end:]
    return text


def _openbook_file(root: Path, relative: str) -> tuple[Path | None, str]:
    requested = (root / relative).resolve()
    if not _inside(requested, root):
        return None, "DENIED: path escapes the selected project root"
    try:
        parts = requested.relative_to(root.resolve()).parts
    except ValueError:
        return None, "DENIED: path escapes the selected project root"
    if any(part in DENIED_PARTS or part.lower().startswith(".env")
           for part in parts):
        return None, "DENIED: private or generated dependency path"
    if requested.suffix.lower() not in TEXT_SUFFIXES:
        return None, "DENIED: only project text files may be read"
    if not requested.is_file():
        return None, "file does not exist"
    return requested, ""


def read_path(repo: Path, workbench: Path, name: str, root_name: str,
              path: str, start: int, end: int) -> str:
    root = repo if root_name == "target" else workbench
    requested, error = _openbook_file(root.resolve(), path)
    if requested is None:
        return error
    text = _redact_target_definition(
        requested.read_text(errors="replace"), name)
    lines = text.splitlines()
    start_index, end_index = start - 1, min(end, len(lines))
    return "\n".join(
        f"{index + 1:5}: {lines[index]}"
        for index in range(start_index, end_index))[:MAX_OPENBOOK_OBSERVATION]


def search_repo(repo: Path, workbench: Path, name: str, root_name: str,
                query: str) -> str:
    if root_name == "both":
        target = search_repo(repo, workbench, name, "target", query)
        local = search_repo(repo, workbench, name, "workbench", query)
        return ("TARGET REPOSITORY:\n" + target +
                "\n\nWORKBENCH:\n" + local)[:MAX_OPENBOOK_OBSERVATION]
    root = (repo if root_name == "target" else workbench).resolve()
    needle = query.casefold()
    rows: list[str] = []
    for relative, raw_text, folded_text in _search_index(root):
        if needle not in folded_text:
            continue
        text = _redact_target_definition(raw_text, name)
        for index, line in enumerate(text.splitlines()):
            if needle not in line.casefold():
                continue
            rows.append(f"{relative}:{index + 1}: {line}")
            if len(rows) >= 40:
                return "\n".join(rows)[:MAX_OPENBOOK_OBSERVATION]
    return ("\n".join(rows)[:MAX_OPENBOOK_OBSERVATION]
            if rows else f"no project text matched {query!r}")


def inspect_diff(candidate: Candidate, packet: residual.ResidualPacket,
                 view: str) -> str:
    diff = diff_annotate.annotate(candidate.attempt.diff or "",
                                  candidate.attempt.source_attribution)
    changed = [line for line in diff.splitlines()
               if line[:1] in {"+", "-"}
               and not line.startswith(("+++", "---"))]
    if view == "first":
        return "\n".join(packet.first_difference)
    if view == "full":
        return diff[:MAX_OBSERVATION]
    if view == "bytes":
        return packet.render()[:MAX_OBSERVATION]
    if view == "layout":
        rows = [line for line in changed if MEMORY_OP.search(line)]
    elif view == "relocation":
        rows = [line for line in changed
                if "%hi(" in line or "%lo(" in line]
    else:
        rows = [line for line in changed
                if len(REGISTER.findall(line)) >= 1]
    return ("\n".join(rows)[:MAX_OBSERVATION]
            if rows else f"no {view} lines in the current residual")


def _packet(candidate: Candidate, asm: str, ws: Path) -> residual.ResidualPacket:
    return residual.build(
        candidate.attempt, target_asm=asm, target_object=ws / "target.o",
        candidate_object=candidate.object_path)


def _rank(candidate: Candidate, packet: residual.ResidualPacket) -> tuple:
    if workspace.repair_complete(candidate.attempt):
        return (-1,)
    if not candidate.attempt.compiled:
        return (2, len(candidate.attempt.compiler_stderr), candidate.candidate_id)
    faults = packet.faults
    classified = sum(faults.values())
    distance = (packet.positional_byte_distance
                if packet.positional_byte_distance is not None else 10**9)
    return (1, faults["structural"], abs(packet.instruction_delta or 0),
            classified, distance, -candidate.attempt.score,
            candidate.candidate_id)


def _summary(candidate: Candidate, packet: residual.ResidualPacket,
             active: bool) -> str:
    return json.dumps({
        "candidate_id": candidate.candidate_id,
        "active": active,
        "parent_id": candidate.parent_id,
        "compiled": candidate.attempt.compiled,
        "exact": candidate.attempt.exact,
        "weighted_progress_score": candidate.attempt.score,
        "instruction_delta": packet.instruction_delta,
        "byte_distance": packet.positional_byte_distance,
        "faults": packet.faults,
        "action": candidate.action,
        **({'semantic': {'status': candidate.semantic.get('status'),
                         'counts': candidate.semantic.get('counts'),
                         'feedback': candidate.semantic.get('feedback', [])[:1]}}
           if candidate.semantic else {}),
    }, sort_keys=True)


def build_prompt(asm: str, active: Candidate, candidates: list[Candidate],
                 packets: dict[str, residual.ResidualPacket],
                 events: list[dict], diagnosis: str = "",
                 open_book: bool = False, policy: str = "",
                 principles: tuple[str, ...] = (),
                 history_override: str | None = None) -> str:
    history = (history_override if history_override is not None else
               "\n".join(json.dumps(event, sort_keys=True) for event in events[-10:]) or "(none)")
    summaries = "\n".join(
        _summary(candidate, packets[candidate.candidate_id],
                 candidate.candidate_id == active.candidate_id)
        for candidate in candidates[-8:])
    template = OPEN_BOOK_PROMPT if open_book else PROMPT
    return template.format(
        asm=asm, active_id=active.candidate_id, source=active.source,
        packet=packets[active.candidate_id].render(),
        diagnosis=diagnosis or "(none)", candidates=summaries,
        history=history,
        policy=policy or "No additional stopping constraint.",
        principles=("\n\n".join(principles)
                    if principles else "No retrieved principles."))


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _validate_reconstruction(old: str, new: str) -> None:
    """Keep preprocessing harness-owned when replacing the complete C body.

    Normalize splices/comments before checking directives. Otherwise a source
    include can bypass the ordinary patch guard through ``#/**/include`` or a
    split directive. Alternative preprocessing token spellings are declined.
    """
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)

    def normalized(source):
        source = source.replace('\r\n', '\n').replace('\r', '\n')
        source = re.sub(r'\\\n', '', source)
        return token.sub(lambda m: (' ' + '\n' * m[0].count('\n'))
                         if m[0].startswith(('/*', '//')) else m[0], source)

    before, after = normalized(old), normalized(new)
    if '??' in after or '%:' in after:
        raise ValueError('replacement uses unsupported preprocessing token spellings')
    directives = lambda text: tuple(line.strip() for line in
                                    re.findall(r'(?m)^[ \t\v\f]*#[^\n]*', text))
    if directives(before) != directives(after):
        raise ValueError('replacement changes harness-owned preprocessing context')
    code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', after)
    if re.search(r'\b(?:asm|__asm|__asm__|GLOBAL_ASM|INCLUDE_ASM)\b|\.incbin\b', code):
        raise ValueError('replacement introduces a forbidden source escape')


def search(repo: Path, name: str, source: str, ws: Path, *, model: str,
           endpoint: str, conn=None,
           base_attempt: workspace.Attempt | None = None,
           base_object_path: Path | None = None,
           parent_attempt_id: int | None = None,
           diagnosis: str = "",
           provider: modelrepair.ProposalProvider | None = None,
           max_calls: int = 6, max_compiles: int = 3,
           require_inspection_before_patch: bool = True,
           timeout: int = 420, think: str = "low", num_thread: int = 12,
           temperature: float = 0.35, num_predict: int = 1200,
           seed: int | None = None, run_id: str = "",
           call_seeds: tuple[int, ...] | None = None,
           cache_dir: str | Path | None = None,
           cache_namespace: str = "", verbose: bool = False,
           open_book: bool = False,
           workbench_root: Path | None = None,
           min_calls_before_finish: int = 0,
           min_compiles_before_finish: int = 0,
           min_tool_actions_before_finish: int = 0,
           principles: tuple[str, ...] = (),
           investigation_tools: dict | None = None, semantic_evaluator=None,
           allow_reconstruction: bool = False, notebook=None,
           max_seconds: float | None = None) -> Result:
    """Run an allowlisted observation/action loop over saved candidates."""
    provider = provider or modelrepair.OllamaProvider()
    if max_seconds is not None and max_seconds <= 0:
        raise ValueError('investigation wall budget must be positive')
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    run_id = run_id or f"toolagent-{time.time_ns()}-{name}"
    config = {
        "max_calls": max_calls, "max_compiles": max_compiles,
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "temperature": temperature, "num_predict": num_predict,
        "seed": seed, "provider": provider.provider_id,
        "call_seeds": list(call_seeds or ()),
        "tools": sorted(ACTIONS), "header_only_reads": not open_book,
        "require_inspection_before_patch": require_inspection_before_patch,
        "open_book": open_book,
        "min_calls_before_finish": min_calls_before_finish,
        "min_compiles_before_finish": min_compiles_before_finish,
        "min_tool_actions_before_finish": min_tool_actions_before_finish,
        "principles": list(principles),
        "allow_reconstruction": allow_reconstruction, "max_seconds": max_seconds,
    }
    if investigation_tools:
        config["investigation_tools"] = sorted(investigation_tools)
        diagnosis += ('\nAdditional investigation actions: '
            '{"action":"inspect_evidence","query":"function_symbol"}; '
            '{"action":"compiler_probe","source":"self-contained C",'
            '"hypothesis":"predicted assembly observation"}. '
            'Probe compilations share the candidate compilation budget. '
            'Save competing explanations with {"action":"record_hypothesis",'
            '"subject":"global:0xADDRESS or local question","alternatives":["cause A","cause B"],'
            '"support":["observation receipt id"]}. '
            'Inspect target binary evidence or its diff before finishing. A missing header alone is not a reason to stop. '
            'Explain competing causes and a distinguishing observation before patching.')
        if 'inspect_compiler' in investigation_tools:
            diagnosis += ('\nInspect ACTIVE C with {"action":"inspect_compiler",'
                '"hypothesis":"what pre-as1 versus final output would distinguish"}. '
                'This reserves two compile units. Only comparable=true phase output may '
                'explain this candidate; target internal compiler phases are unknown.')
        if 'execution_case' in investigation_tools:
            from solver.execution_experiment import CASE_BRIEF
            diagnosis += ('\nPropose target-first execution with {"action":"execution_case",'
                '"hypothesis":"path or boundary to exercise","inputs":{...}}. ' + CASE_BRIEF)
        if 'request_capability' in investigation_tools:
            diagnosis += ('\nRequest isolated solver engineering with {"action":"request_capability",'
                '"hypothesis":"shared tool defect","task":{"issue_key":"known shared issue",'
                '"modules":["solver/module.py"],"reproduce":["tests/test_x.py::test_reproduction"],'
                '"regression":["tests/test_x.py"],"transfer":["tests/test_y.py::test_transfer"]}}. '
                'Only existing tests and scoped implementation modules are allowed. '
                'A request cannot deploy code or change a verifier.')
        if 'inspect_capabilities' in investigation_tools:
            diagnosis += ('\nDiscover existing implementation APIs and test names with '
                '{"action":"inspect_capabilities","query":"parser or capability keyword"}. '
                'This reads declaration names only, never source bodies or test literals.')
    if allow_reconstruction:
        diagnosis += ('\nYou may construct a different complete candidate using '
            '{"action":"replace_source","hypothesis":"structural cause and expected change",'
            '"source":"complete generated C"}. Preserve required declarations and interfaces. '
            'This grants no access to reference C or repository source files. '
            'Use a new representation when local edits have exhausted their hypothesis.')
    if conn is not None:
        attempt_receipts.start_run(
            conn, run_id, kind="tool-agent-repair", model=model, config=config)

    base_tag = f"{name}_toolagent_base"
    base = base_attempt or workspace.score(ws, repo, base_tag, source)
    if base_attempt is None and base.compiled:
        base_object_path = ws / f"{base_tag}.o"
    if parent_attempt_id is not None:
        base.receipt_id = parent_attempt_id
    elif conn is not None and base.receipt_id is None:
        workspace.record_attempt(
            conn, name, source, base, strategy="toolagent-baseline",
            run_id=run_id, run_kind="tool-agent-repair", run_config=config)

    root = Candidate("c0", source, base, base_object_path)
    result = Result(name, root, root, exact=workspace.repair_complete(base))
    if workspace.repair_complete(base) or max_calls <= 0:
        return result

    asm = workspace.target_asm(ws, name)
    candidates = [root]
    result.candidates = candidates
    if semantic_evaluator:
        root.semantic = semantic_evaluator(modelrepair.CandidateState(root.source, root.attempt, root.object_path))
    packets = {"c0": _packet(root, asm, ws)}
    active = root
    seen = {_digest(source)}
    seen_tool_requests: set[tuple] = set()
    workbench_root = (workbench_root or Path.cwd()).resolve()
    policy = (
        "Finish is accepted only after at least "
        f"{min_calls_before_finish} model calls, "
        f"{min_compiles_before_finish} compiled experiments, and "
        f"{min_tool_actions_before_finish} evidence-gathering tool actions. "
        "A rejected finish consumes the call; choose a materially different "
        "inspection or source experiment next."
        if any((min_calls_before_finish, min_compiles_before_finish,
                min_tool_actions_before_finish))
        else "You may finish whenever the evidence justifies it.")

    def record(event, parent, child=None):
        if notebook is not None:
            def measured(candidate):
                packet = packets[candidate.candidate_id]
                return {'score': candidate.attempt.score, 'compiled': candidate.attempt.compiled,
                        'exact': workspace.repair_complete(candidate.attempt),
                        'faults': packet.to_dict().get('faults'),
                        'diff': candidate.attempt.diff[:3000],
                        'semantic_status': (candidate.semantic or {}).get('status')}
            notebook.append({'action': event.get('action', 'generation'),
                'hypothesis': event.get('hypothesis', ''), 'status': event['status'],
                'parent_source_sha256': _digest(parent.source),
                'child_source_sha256': _digest(child.source) if child else None,
                'before': measured(parent), 'after': measured(child) if child else None,
                'metadata': {'observation': event.get('observation', '')[:6000],
                             'error': event.get('error', ''), 'run_id': run_id,
                             'context_budget': event.get('context_budget')}})
        result.events.append(event)

    for step in range(1, max_calls + 1):
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            record({'step': step, 'status': 'wall-budget-exhausted'}, active)
            break
        advanced = allow_reconstruction and not open_book
        remembered = notebook.format_context(_digest(active.source), max_chars=10000) if notebook else ''
        available = None
        response_schema = None
        if advanced:
            from solver import investigation_prompt
            available = investigation_prompt.choices(result.events, active.candidate_id,
                investigation_tools, max_compiles - result.compiles)
            response_schema = investigation_prompt.schema(available)
        def make_prompt(memory_view, history_view=None):
            candidate_prompt = build_prompt(
                asm, active, candidates, packets, result.events,
                diagnosis=diagnosis + '\n' + memory_view, open_book=open_book,
                policy=policy, principles=principles, history_override=history_view)
            if advanced:
                candidate_prompt = candidate_prompt.replace(
                    'No inline assembly, includes, complete-file replacement, or comment-only edits.',
                    'No inline assembly or comment-only edits. Complete replacements must '
                    'preserve all existing preprocessing directives, including includes; '
                    'the harness owns that context.')
                candidate_prompt = investigation_prompt.expose(
                    candidate_prompt, available, max_calls - step + 1,
                    max_compiles - result.compiles, name)
                from solver import prompt_compaction
                candidate_prompt = prompt_compaction.compact(candidate_prompt)
            return candidate_prompt
        prompt = make_prompt(remembered)
        if advanced:
            from solver import prompt_budget
            budget_error = None
            for history_chars, memory_chars in ((None, 10000), (12000, 6000),
                                                (8000, 4000), (4000, 2000),
                                                (2000, 0), (0, 0)):
                if history_chars is not None:
                    projected = investigation_prompt.history_view(result.events, history_chars)
                    memory_view = (notebook.format_context(_digest(active.source), max_chars=memory_chars)
                                   if notebook and memory_chars else '')
                    prompt = make_prompt(memory_view, projected)
                try:
                    prompt_budget.context_budget(prompt, num_predict,
                        prefill='', response_schema=response_schema)
                    budget_error = None
                    break
                except prompt_budget.ContextBudgetError as exc:
                    budget_error = exc
            if budget_error is not None:
                record({'step': step, 'status': 'context-budget-exhausted',
                        'error': str(budget_error),
                        'context_budget': budget_error.context_budget}, active)
                break
        workspace.assert_uncontaminated(prompt, repo, name)
        call_seed = (
            call_seeds[step - 1]
            if call_seeds is not None and step - 1 < len(call_seeds)
            else (None if seed is None else seed + step))
        request = modelrepair.GenerationRequest(
            prompt=prompt, model=model, endpoint=endpoint,
            timeout=min(timeout, max(1, int(remaining))) if remaining is not None else timeout,
            think=think, num_thread=num_thread, temperature=temperature,
            num_predict=num_predict, seed=call_seed, cache_dir=cache_dir,
            cache_namespace=cache_namespace, prefill='' if response_schema else '{"action":"',
            response_schema=response_schema)
        started = time.time()
        result.calls_attempted += 1
        try:
            text, meta = provider.generate(request)
        except Exception as exc:
            from solver.prompt_budget import ContextBudgetError
            wall_ms = int((time.time() - started) * 1000)
            if conn is not None:
                attempt_receipts.record_model_proposal(
                    conn, run_id=run_id,
                    parent_attempt_id=active.attempt.receipt_id,
                    prompt=prompt, raw_response=str(exc),
                    status="generation-error", model=model,
                    sampling={"seed": call_seed,
                              "provider": provider.provider_id},
                    wall_ms=wall_ms)
            record({
                "step": step,
                "status": ("context-budget-exhausted" if isinstance(exc, ContextBudgetError)
                           else "generation-error"),
                "error": str(exc) if isinstance(exc, ContextBudgetError) else type(exc).__name__,
                **({"context_budget": exc.context_budget}
                   if isinstance(exc, ContextBudgetError) else {})}, active)
            if isinstance(exc, ContextBudgetError):
                break
            continue

        wall_ms = int((time.time() - started) * 1000)
        result.generations += 1
        result.tokens += int(meta.get("eval_count", 0) or 0)
        if not meta.get("_cache_hit"):
            result.charged_tokens += int(meta.get("eval_count", 0) or 0)
        action = None
        status = "valid"
        error = ""
        if llm.is_refusal(text):
            status, error = "refusal", "model refused"
        else:
            try:
                action = parse_action(text)
            except ValueError as exc:
                status, error = "invalid", str(exc)

        observation = ""
        child: Candidate | None = None
        parent = active
        if deadline is not None and time.monotonic() >= deadline:
            status, error = 'wall-budget-exhausted', 'model returned after investigation deadline; no action executed'
        if action is not None and status == "valid":
            if available is not None and action.name not in available:
                status, error = 'unavailable-tool', 'action disabled after repeated attempts or exhausted budget'
            if action.name in OPEN_BOOK_ACTIONS and not open_book and not (
                    action.name == 'replace_source' and allow_reconstruction):
                status, error = "unavailable-tool", \
                    "open-book action is unavailable in bounded mode"
            tool_key = (action.name, action.query, action.path, action.start,
                        action.end, action.view, action.candidate_id,
                        action.root, _digest(action.source) if action.source else '',
                        _digest(active.source) if action.name in {'inspect_diff', *CONTEXT_ACTIONS} else '',
                        json.dumps(action.payload, sort_keys=True))
            if (not open_book
                    and action.name in INSPECTION_ACTIONS | {"select_candidate"}) \
                    and tool_key in seen_tool_requests:
                status, error = "duplicate-tool", \
                    "identical tool request already executed; choose a new action"
            elif (not open_book
                  and action.name in INSPECTION_ACTIONS | {"select_candidate"}):
                seen_tool_requests.add(tool_key)

            if status != "valid":
                pass
            elif action.name in {"inspect_evidence", "compiler_probe", "record_hypothesis", "inspect_capabilities", *CONTEXT_ACTIONS}:
                cost = {'compiler_probe': 1, 'inspect_compiler': 2}.get(action.name, 0)
                if not investigation_tools or action.name not in investigation_tools:
                    status, error = "unavailable", "investigation tool not configured"
                elif result.compiles + cost > max_compiles:
                    status, error = "compile-budget-exhausted", "probe compilation budget exhausted"
                else:
                    result.compiles += cost
                    try:
                        handler = investigation_tools[action.name]
                        observation = handler(action, active) if action.name in CONTEXT_ACTIONS else handler(action)
                    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
                        status, error = "tool-failed", str(exc)
                    if action.name == 'execution_case' and status == 'valid' and semantic_evaluator:
                        active.semantic = semantic_evaluator(modelrepair.CandidateState(
                            active.source, active.attempt, active.object_path))
            elif action.name == "inspect_definition":
                observation = inspect_definition(repo, action.query)
            elif action.name == "read_header":
                observation = read_header(
                    repo, action.path, action.start, action.end)
            elif action.name == "search_repo":
                observation = search_repo(
                    repo, workbench_root, name, action.root, action.query)
            elif action.name == "read_path":
                observation = read_path(
                    repo, workbench_root, name, action.root, action.path,
                    action.start, action.end)
            elif action.name == "inspect_diff":
                observation = inspect_diff(
                    active, packets[active.candidate_id], action.view)
            elif action.name == "inspect_history":
                observation = ("\n".join(json.dumps(event, sort_keys=True)
                                           for event in result.events[-10:])
                               or "no prior actions")
                if notebook:
                    observation = notebook.format_context(_digest(active.source), max_chars=6000) + '\n' + observation
            elif action.name == "select_candidate":
                selected = next((candidate for candidate in candidates
                                 if candidate.candidate_id == action.candidate_id),
                                None)
                if selected is None:
                    status, error = "invalid", "unknown candidate id"
                else:
                    active = selected
                    observation = f"active candidate is now {active.candidate_id}"
            elif action.name == "finish":
                unmet = []
                if investigation_tools and not any(e.get('action') in {'inspect_evidence', 'inspect_diff', 'compiler_probe',
                                                                       'inspect_compiler', 'execution_case'}
                                                   and e.get('status') == 'valid' for e in result.events):
                    unmet.append('target evidence or diff inspection before finish')
                if step < min_calls_before_finish:
                    unmet.append(f"calls {step}/{min_calls_before_finish}")
                if result.compiles < min_compiles_before_finish:
                    unmet.append(
                        f"compiles {result.compiles}/"
                        f"{min_compiles_before_finish}")
                if result.tool_actions < min_tool_actions_before_finish:
                    unmet.append(
                        f"tool actions {result.tool_actions}/"
                        f"{min_tool_actions_before_finish}")
                if unmet:
                    status, error = "curiosity-budget-unmet", \
                        "finish rejected: " + ", ".join(unmet)
                else:
                    observation = action.reason or "model chose to finish"
            elif (action.name in {"patch", "replace_source"}
                  and require_inspection_before_patch
                  and result.tool_actions == 0):
                status, error = "inspection-required", \
                    "at least one inspection tool must run before the first patch"
            elif result.compiles >= max_compiles:
                status, error = "compile-budget-exhausted", \
                    "patch rejected because compile budget is exhausted"
            elif action.proposal is not None:
                try:
                    candidate_source = modelrepair.apply_proposal(
                        active.source, action.proposal)
                except ValueError as exc:
                    status, error = "invalid", str(exc)
                else:
                    digest = _digest(candidate_source)
                    if digest in seen:
                        status, error = "duplicate", "source already evaluated"
                    else:
                        seen.add(digest)
                        child_id = f"c{len(candidates)}"
                        child_tag = f"{name}_toolagent_{step}_{child_id}"
                        proposal_label = (
                            f"{action.proposal.kind}: "
                            f"{action.proposal.hypothesis}")
                        # Proposal receipt is created below, before the child is
                        # linked, so keep the compile metadata ready here.
                        child = Candidate(
                            child_id, candidate_source,
                            workspace.Attempt(False, 0.0, False, "", "", ""),
                            None, active.candidate_id, proposal_label)
            elif action.name == "replace_source":
                try:
                    _validate_reconstruction(active.source, action.source)
                    workspace.assert_uncontaminated(action.source, repo, name)
                except ValueError as exc:
                    status, error = "invalid", str(exc)
                except RuntimeError as exc:
                    status, error = "contamination", str(exc)
                else:
                    digest = _digest(action.source)
                    if digest in seen:
                        status, error = "duplicate", "source already evaluated"
                    else:
                        seen.add(digest)
                        child_id = f"c{len(candidates)}"
                        child = Candidate(
                            child_id, action.source,
                            workspace.Attempt(False, 0.0, False, "", "", ""),
                            None, active.candidate_id,
                            f"full-rewrite: {action.hypothesis}")

        edits = ([asdict(edit) for edit in action.proposal.edits]
                 if action and action.proposal else [])
        proposal_id = None
        if conn is not None:
            proposal_id = attempt_receipts.record_model_proposal(
                conn, run_id=run_id,
                parent_attempt_id=active.attempt.receipt_id,
                prompt=prompt, raw_response=text, status=(
                    "tool-executed" if observation and action
                    and action.name in INSPECTION_ACTIONS else status),
                model=model,
                kind=(f"tool:{action.name}" if action else ""),
                hypothesis=(action.proposal.hypothesis
                            if action and action.proposal else
                            (action.hypothesis or action.query)
                            if action else ""),
                edits=edits,
                sampling={
                    "seed": call_seed, "provider": provider.provider_id,
                    "action": action.name if action else "",
                    "observation": observation[:(
                        MAX_OPENBOOK_OBSERVATION if open_book
                        else MAX_OBSERVATION)],
                    "cache_hit": bool(meta.get("_cache_hit")),
                    'response_schema_sha256': _digest(json.dumps(response_schema, sort_keys=True)) if response_schema else None,
                    'done_reason': meta.get('done_reason'),
                    'fell_back_to_thinking': bool(meta.get('_fell_back_to_thinking')),
                    'context_budget': meta.get('_context_budget'),
                },
                wall_ms=wall_ms, token_cost=meta.get("eval_count", 0))

        event = {
            "step": step, "action": action.name if action else "invalid",
            "status": status, "active_candidate": active.candidate_id,
            'hypothesis': (action.proposal.hypothesis if action and action.proposal else
                           action.hypothesis if action else ''),
        }
        if error:
            event["error"] = error
            result.invalid_actions += 1
        if observation:
            event["observation"] = observation[:(
                MAX_OPENBOOK_OBSERVATION if open_book else MAX_OBSERVATION)]
            if action and action.name in INSPECTION_ACTIONS:
                result.tool_actions += 1

        if child is not None and action:
            result.compiles += 1
            child_tag = f"{name}_toolagent_{step}_{child.candidate_id}"
            attempt = workspace.score(
                ws, repo, child_tag, child.source, conn=conn, func=name,
                iteration=step,
                strategy=("toolagent-patch" if action.proposal
                          else "toolagent-replace-source"), model=model,
                prompt=prompt, temperature=temperature, wall_ms=wall_ms,
                run_id=run_id, token_cost=meta.get("eval_count", 0),
                extra={"seed": call_seed, "proposal_id": proposal_id,
                       "provider": provider.provider_id,
                       "tool_actions_before_patch": result.tool_actions},
                raw_response=text,
                extract_status=("tool-action-patch" if action.proposal
                                else "tool-action-replace-source"),
                done_reason=meta.get("done_reason", ""),
                parent_attempt_id=active.attempt.receipt_id,
                relation="tool-agent-patch", action=child.action,
                feedback=packets[active.candidate_id].render(),
                run_kind="tool-agent-repair", run_config=config)
            child.attempt = attempt
            child.object_path = ws / f"{child_tag}.o" if attempt.compiled else None
            if semantic_evaluator:
                child.semantic = semantic_evaluator(modelrepair.CandidateState(child.source, attempt, child.object_path))
            candidates.append(child)
            packets[child.candidate_id] = _packet(child, asm, ws)
            active = child
            event.update({
                "child_candidate": child.candidate_id,
                "compiled": attempt.compiled, "exact": attempt.exact,
                "score": attempt.score,
                "residual": packets[child.candidate_id].to_dict(),
            })
            if conn is not None and proposal_id is not None and attempt.receipt_id:
                attempt_receipts.link_model_proposal(
                    conn, proposal_id, attempt.receipt_id)
            best = min(candidates,
                       key=lambda candidate: _rank(
                           candidate, packets[candidate.candidate_id]))
            result.best = best
            if workspace.repair_complete(attempt):
                result.exact = True
                record(event, parent, child)
                return result

        record(event, parent, child)
        if verbose:
            print(f"      tool step {step}: {event}", flush=True)
        if action and action.name == "finish" and status == "valid":
            break

    result.exact = workspace.repair_complete(result.best.attempt)
    return result

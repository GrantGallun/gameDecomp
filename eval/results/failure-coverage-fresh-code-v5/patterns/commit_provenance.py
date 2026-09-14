"""Deterministic provenance reports for functions in a decompilation history.

The report separates three facts that are easy to blur together:

* component: where the current source lives (game, SDK, middleware)
* origin: what the commit history explicitly says the source was based on
* reasoning record: what explanation survives (messages and session links)

No origin is inferred from code similarity.  In particular, a function with no
explicit upstream or sibling evidence remains ``unknown``.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


LOG_RECORD = "\x1e"
LOG_FIELD = "\x1f"
LOG_MESSAGE_END = "\x1d"

IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
SCORE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*%")
CLAUDE_SESSION = re.compile(
    r"https://claude\.ai/code/session_[A-Za-z0-9_-]+", re.IGNORECASE)
GITHUB_REF = re.compile(
    r"https?://github\.com/(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"
    r"(?:/(?:commit|tree)/(?P<commit>[0-9a-f]{7,40}))?",
    re.IGNORECASE,
)
TEXT_REPO_REF = re.compile(
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s+"
    r"(?:at\s+)?commit\s+(?P<commit>[0-9a-f]{7,40})",
    re.IGNORECASE,
)
UPSTREAM_CUE = re.compile(
    r"\b(?:port(?:ed)?(?:\s+from)?|copied\s+from)\s+upstream\b|"
    r"\bupstream\s+[A-Za-z0-9_.-]+\.(?:c|h)\b",
    re.IGNORECASE,
)
SIBLING_CUE = re.compile(
    r"\b(?:twin|sibling|verbatim|mirror(?:ed|ing)?)\b", re.IGNORECASE)
COPY_CUE = re.compile(
    r"\b(?:cop(?:y|ied|ying)|body)\b.{0,80}\bverbatim\b|"
    r"\bverbatim\b.{0,80}\b(?:cop(?:y|ied|ying)|body)\b|"
    r"\b(?:twin|sibling)\s+of\b",
    re.IGNORECASE | re.DOTALL,
)
EXACT_CUE = re.compile(
    r"\b(?:now exact|byte[- ]exact|matching 100%|matched 100%|"
    r"reached 100%|reaches 100%)\b",
    re.IGNORECASE,
)

CONTROL_WORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "defined",
    "do", "else", "case",
}


class GitHistoryError(RuntimeError):
    """A Git query required for the report failed."""


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    old_path: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"status": self.status, "path": self.path}
        if self.old_path is not None:
            result["old_path"] = self.old_path
        return result


@dataclass(frozen=True)
class Commit:
    sha: str
    authored_at: str
    author: str
    message: str
    changes: tuple[Change, ...]

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


def _git(repo: Path, args: Iterable[str], *, allow_failure: bool = False) -> str | None:
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        if allow_failure:
            return None
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise GitHistoryError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def read_history(repo: Path) -> list[Commit]:
    """Read all refs oldest-first, including messages and changed paths."""
    fmt = f"{LOG_RECORD}%H{LOG_FIELD}%aI{LOG_FIELD}%an{LOG_FIELD}%B{LOG_MESSAGE_END}"
    raw = _git(repo, ["log", "--all", "--reverse", f"--format={fmt}",
                      "--name-status"])
    assert raw is not None
    commits: list[Commit] = []
    for record in raw.split(LOG_RECORD):
        if not record.strip() or LOG_MESSAGE_END not in record:
            continue
        metadata, changed = record.split(LOG_MESSAGE_END, 1)
        fields = metadata.split(LOG_FIELD, 3)
        if len(fields) != 4:
            continue
        sha, authored_at, author, message = fields
        changes = tuple(_parse_changes(changed))
        commits.append(Commit(
            sha=sha.strip(),
            authored_at=authored_at.strip(),
            author=author.strip(),
            message=message.strip(),
            changes=changes,
        ))
    return commits


def _parse_changes(raw: str) -> list[Change]:
    changes: list[Change] = []
    for line in raw.strip().splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        status = fields[0]
        if status.startswith(("R", "C")) and len(fields) >= 3:
            changes.append(Change(status=status, old_path=fields[1], path=fields[2]))
        else:
            changes.append(Change(status=status, path=fields[1]))
    return changes


def _mask_non_code(source: str) -> str:
    """Blank comments and literals while preserving offsets and newlines."""
    chars = list(source)
    i = 0
    state = "code"
    quote = ""
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if state == "code":
            if c == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if c == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if c in {'"', "'"}:
                quote = c
                chars[i] = " "
                i += 1
                state = "literal"
                continue
        elif state == "line_comment":
            if c == "\n":
                state = "code"
            else:
                chars[i] = " "
        elif state == "block_comment":
            if c == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "code"
                continue
            if c != "\n":
                chars[i] = " "
        else:
            if c == "\\" and nxt:
                chars[i] = " "
                if nxt != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if c == quote:
                chars[i] = " "
                state = "code"
            elif c != "\n":
                chars[i] = " "
        i += 1
    return "".join(chars)


def _matching_delimiter(masked: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for pos in range(start, len(masked)):
        if masked[pos] == opening:
            depth += 1
        elif masked[pos] == closing:
            depth -= 1
            if depth == 0:
                return pos
    return None


def function_definitions(source: str) -> dict[str, str]:
    """Return ANSI C function definitions found in *source*.

    This is intentionally a lexical recognizer rather than a C parser.  It
    requires ``name(...)`` to be followed by ``{`` and masks comments and
    literals first, which is sufficient for the source styles in N64 decomps.
    """
    masked = _mask_non_code(source)
    found: dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", masked):
        name = match.group(1)
        if name in CONTROL_WORDS:
            continue
        line_start = masked.rfind("\n", 0, match.start()) + 1
        if masked[line_start:match.start()].lstrip().startswith("#"):
            continue
        open_paren = masked.find("(", match.start())
        close_paren = _matching_delimiter(masked, open_paren, "(", ")")
        if close_paren is None:
            continue
        pos = close_paren + 1
        while pos < len(masked) and masked[pos].isspace():
            pos += 1
        if pos >= len(masked) or masked[pos] != "{":
            continue
        close_brace = _matching_delimiter(masked, pos, "{", "}")
        if close_brace is None:
            continue
        found.setdefault(name, source[line_start:close_brace + 1].strip())
    return found


def extract_function(source: str | None, name: str) -> str | None:
    if source is None:
        return None
    return function_definitions(source).get(name)


def action_for(subject: str) -> str:
    lower = subject.lower()
    if lower.startswith("improve "):
        return "improve"
    if lower.startswith("match "):
        return "match"
    if lower.startswith(("decompile ", "implement ", "recover ")):
        return "introduce"
    if lower.startswith(("record ", "investigate ", "diagnose ")):
        return "investigate"
    if lower.startswith(("fix ", "correct ")):
        return "fix"
    if lower.startswith(("refactor ", "cleanup ", "clean up ")):
        return "cleanup"
    return "other"


def scores_in(message: str) -> list[float]:
    return list(dict.fromkeys(float(value) for value in SCORE.findall(message)))


def session_urls(message: str) -> list[str]:
    return list(dict.fromkeys(CLAUDE_SESSION.findall(message)))


def upstream_refs(message: str) -> list[dict[str, str | None]]:
    refs: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for match in GITHUB_REF.finditer(message):
        value = (match.group("repo"), match.group("commit"), match.group(0))
        if value not in seen:
            seen.add(value)
            refs.append({"repo": value[0], "commit": value[1], "url": value[2]})
    for match in TEXT_REPO_REF.finditer(message):
        value = (match.group("repo"), match.group("commit"), None)
        if value not in seen:
            seen.add(value)
            refs.append({"repo": value[0], "commit": value[1], "url": None})
    if UPSTREAM_CUE.search(message) and not refs:
        refs.append({"repo": None, "commit": None, "url": None})
    return refs


def sibling_refs(message: str, target: str, known_names: set[str]) -> list[str]:
    refs: list[str] = []
    # Keep the evidence boundary at the sentence containing the cue.  A broad
    # character window incorrectly turns later callees and callbacks into
    # alleged siblings.
    sentences = re.split(r"(?<=[.!?])\s+|\n\s*\n", message)
    for sentence in sentences:
        if not SIBLING_CUE.search(sentence):
            continue
        for name in IDENT.findall(sentence):
            if name == target:
                continue
            if name in known_names or name.startswith("func_"):
                if name not in refs:
                    refs.append(name)
    return refs


def _explicit_subject_targets(subject: str, known_names: set[str]) -> list[str]:
    patterns = (
        r"(?i)^(?:match|improve|decompile|implement|recover)\s+([A-Za-z_]\w*)\b",
        r"(?i)^record\b.{0,80}\b(?:of|for)\s+([A-Za-z_]\w*)\b",
        r"(?i)^(?:fix|correct)\b.{0,100}\bin\s+([A-Za-z_]\w*)\b",
    )
    targets: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, subject)
        if match:
            candidate = match.group(1)
            if candidate in known_names or candidate.startswith("func_"):
                targets.append(candidate)
            break
    if targets:
        return targets
    # Non-standard but useful subjects such as "Measure foo residual".
    return list(dict.fromkeys(
        token for token in IDENT.findall(subject) if token in known_names))


def _component(paths: list[str]) -> dict[str, object]:
    normalized = [path.replace("\\", "/").lower() for path in paths]
    sdk = [path for path, low in zip(paths, normalized)
           if low.startswith(("src/ultra/", "src/libultra/", "lib/ultra/"))]
    middleware = [path for path, low in zip(paths, normalized)
                  if "libmus" in low or low.startswith(("src/mus/", "lib/mus/"))]
    game = [path for path, low in zip(paths, normalized)
            if low.startswith("src/") and path not in sdk and path not in middleware]
    if sdk:
        return {"kind": "sdk", "evidence": sorted(set(sdk))}
    if middleware:
        return {"kind": "middleware", "evidence": sorted(set(middleware))}
    if game:
        return {"kind": "game", "evidence": sorted(set(game))}
    return {"kind": "unknown", "evidence": []}


def _repo_slug(remote: str | None) -> str | None:
    if not remote:
        return None
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", remote)
    return match.group(1) if match else None


def _origin(events: list[dict[str, object]], remote_slug: str | None,
            component_kind: str) -> dict[str, object]:
    refs = [ref for event in events for ref in event["upstream_refs"]]  # type: ignore[index]
    siblings = list(dict.fromkeys(
        ref for event in events for ref in event["sibling_refs"]))  # type: ignore[index]
    messages = "\n".join(str(event["message"]) for event in events)
    evidence: list[str] = []
    if refs:
        repos = [str(ref["repo"]) for ref in refs if ref.get("repo")]
        if repos and remote_slug and all(
                repo.split("/", 1)[-1].lower() == remote_slug.split("/", 1)[-1].lower()
                for repo in repos):
            kind = "same_game_fork"
        elif repos:
            kind = "explicit_upstream"
        else:
            kind = "upstream_unspecified"
        evidence.extend(
            f"{ref.get('repo') or 'upstream'}"
            f"{(' commit ' + str(ref['commit'])) if ref.get('commit') else ''}"
            for ref in refs)
        return {"kind": kind, "evidence": list(dict.fromkeys(evidence)),
                "upstream_refs": refs, "sibling_refs": siblings}
    if siblings:
        if component_kind == "game":
            kind = ("same_game_sibling" if COPY_CUE.search(messages)
                    else "same_game_analogue")
        else:
            kind = ("same_component_sibling" if COPY_CUE.search(messages)
                    else "same_component_analogue")
        return {"kind": kind, "evidence": siblings,
                "upstream_refs": [], "sibling_refs": siblings}
    return {"kind": "unknown", "evidence": [],
            "upstream_refs": [], "sibling_refs": []}


def _event(commit: Commit, target: str, known_names: set[str],
           *, introduced_by_file_add: bool = False) -> dict[str, object]:
    scores = scores_in(commit.message)
    action = action_for(commit.subject)
    if introduced_by_file_add and action == "other":
        action = "introduce_source"
    return {
        "sha": commit.sha,
        "authored_at": commit.authored_at,
        "author": commit.author,
        "subject": commit.subject,
        "message": commit.message,
        "action": action,
        "scores": scores,
        "exact": action == "match" or 100.0 in scores or bool(EXACT_CUE.search(commit.message)),
        "changes": [change.as_dict() for change in commit.changes],
        "claude_sessions": session_urls(commit.message),
        "upstream_refs": upstream_refs(commit.message),
        "sibling_refs": sibling_refs(commit.message, target, known_names),
    }


def _compact_commit(commit: Commit, path: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "sha": commit.sha,
        "authored_at": commit.authored_at,
        "author": commit.author,
        "subject": commit.subject,
    }
    if path is not None:
        result["path"] = path
        result["evidence"] = "function definition absent before commit and present after"
    return result


def _reasoning_record(events: list[dict[str, object]]) -> dict[str, object]:
    sessions = list(dict.fromkeys(
        url for event in events for url in event["claude_sessions"]))  # type: ignore[index]
    substantive = sum(
        len(str(event["message"]).split()) - len(str(event["subject"]).split()) >= 8
        for event in events)
    improvements = sum(event["action"] == "improve" for event in events)
    if sessions:
        availability = "linked_sessions"
    elif substantive >= 2 or improvements:
        availability = "incremental_commit_messages"
    elif substantive == 1:
        availability = "commit_message"
    else:
        availability = "subjects_only"
    return {
        "availability": availability,
        "commit_messages": len(events),
        "substantive_messages": substantive,
        "improvement_commits": improvements,
        "claude_sessions": sessions,
        "session_note": (
            "Links are provenance pointers; the report does not assert that their contents are accessible."
            if sessions else None),
    }


def build_report(repo: Path, *, functions: Iterable[str] = (),
                 include_source: bool = False) -> dict[str, object]:
    """Build a deterministic report from local Git objects only."""
    repo = repo.expanduser().resolve()
    history = read_history(repo)
    by_sha = {commit.sha: commit for commit in history}
    commit_order = {commit.sha: index for index, commit in enumerate(history)}
    head = (_git(repo, ["rev-parse", "HEAD"]) or "").strip()
    remote = _git(repo, ["remote", "get-url", "origin"], allow_failure=True)
    remote = remote.strip() if remote else None

    tracked_raw = _git(repo, ["ls-files", "-z", "--", "*.c"]) or ""
    tracked_c = [path for path in tracked_raw.split("\0") if path]

    @lru_cache(maxsize=384)
    def blob(revision: str, path: str) -> str | None:
        return _git(repo, ["show", f"{revision}:{path}"], allow_failure=True)

    current_paths: dict[str, set[str]] = defaultdict(set)
    for path in tracked_c:
        source = blob("HEAD", path)
        if source is None:
            continue
        for name in function_definitions(source):
            current_paths[name].add(path)
    known_names = set(current_paths)

    histories: dict[str, list[dict[str, object]]] = defaultdict(list)
    history_shas: dict[str, set[str]] = defaultdict(set)
    first_c: dict[str, tuple[Commit, str]] = {}
    attributed_shas: set[str] = set()

    # Message targets preserve Improve/Match/investigation sequences.
    for commit in history:
        for target in _explicit_subject_targets(commit.subject, known_names):
            histories[target].append(_event(commit, target, known_names))
            history_shas[target].add(commit.sha)
            attributed_shas.add(commit.sha)

    # A newly-added C file is strong source-introduction evidence even when
    # the subject names a segment rather than each function in it.
    for commit in history:
        for change in commit.changes:
            if change.status != "A" or not change.path.endswith(".c"):
                continue
            source = blob(commit.sha, change.path)
            if source is None:
                continue
            for target in function_definitions(source):
                if commit.sha not in history_shas[target]:
                    histories[target].append(_event(
                        commit, target, known_names, introduced_by_file_add=True))
                    history_shas[target].add(commit.sha)
                first_c.setdefault(target, (commit, change.path))
                attributed_shas.add(commit.sha)

    # Resolve introductions in existing C files when the message names the
    # function.  This checks source state on both sides instead of assuming
    # that "Match" means "first C commit".
    for target, events in histories.items():
        if target in first_c:
            continue
        for event in events:
            commit = by_sha[str(event["sha"])]
            for change in commit.changes:
                if not change.path.endswith(".c"):
                    continue
                before_path = change.old_path or change.path
                before = extract_function(blob(f"{commit.sha}^", before_path), target)
                after = extract_function(blob(commit.sha, change.path), target)
                if after is not None and before is None:
                    first_c[target] = (commit, change.path)
                    break
            if target in first_c:
                break

    selected = list(dict.fromkeys(functions))
    if selected:
        missing = [name for name in selected if name not in histories and name not in current_paths]
        if missing:
            raise ValueError("functions not found in source or history: " + ", ".join(missing))
        names = selected
    else:
        names = sorted(histories)

    # A filtered/deep query earns a slower pickaxe fallback for functions
    # whose introduction was not named in its commit message.
    if selected:
        for target in selected:
            if target not in first_c:
                resolved = _pickaxe_first_source(repo, target, by_sha, blob)
                if resolved is not None:
                    first_c[target] = resolved

    function_reports: list[dict[str, object]] = []
    for target in names:
        events = sorted(histories.get(target, []),
                        key=lambda item: commit_order[str(item["sha"])])
        paths = sorted(set(current_paths.get(target, set())) | {
            str(change["path"])
            for event in events
            for change in event["changes"]  # type: ignore[union-attr]
            if str(change["path"]).endswith(".c")
        })
        component = _component(paths)
        report: dict[str, object] = {
            "function": target,
            "current_paths": sorted(current_paths.get(target, set())),
            "component": component,
            "origin": _origin(events, _repo_slug(remote), str(component["kind"])),
            "first_history_commit": (
                {key: events[0][key] for key in
                 ("sha", "authored_at", "author", "subject")}
                if events else None),
            "first_c_commit": (
                _compact_commit(*first_c[target]) if target in first_c else None),
            "first_c_status": "resolved" if target in first_c else "not_resolved",
            "reasoning_record": _reasoning_record(events),
            "history": events,
        }
        if include_source:
            report["source_transitions"] = _source_transitions(target, events, by_sha, blob)
        function_reports.append(report)

    unattributed = []
    for commit in history:
        if commit.sha in attributed_shas:
            continue
        if action_for(commit.subject) not in {"match", "improve", "introduce"}:
            continue
        unattributed.append({
            "sha": commit.sha,
            "authored_at": commit.authored_at,
            "subject": commit.subject,
            "message": commit.message,
            "changes": [change.as_dict() for change in commit.changes],
            "claude_sessions": session_urls(commit.message),
            "upstream_refs": upstream_refs(commit.message),
        })

    component_counts = Counter(
        str(report["component"]["kind"]) for report in function_reports)  # type: ignore[index]
    origin_counts = Counter(
        str(report["origin"]["kind"]) for report in function_reports)  # type: ignore[index]
    return {
        "schema_version": 1,
        "repository": {
            "path": str(repo),
            "head": head,
            "remote": remote,
            "commits_scanned": len(history),
        },
        "summary": {
            "functions": len(function_reports),
            "first_c_resolved": sum(report["first_c_commit"] is not None
                                    for report in function_reports),
            "component_counts": dict(sorted(component_counts.items())),
            "origin_counts": dict(sorted(origin_counts.items())),
            "unattributed_matching_commits": len(unattributed),
        },
        "functions": function_reports,
        "unattributed_commits": unattributed,
    }


def _changes_for_sha(repo: Path, sha: str) -> tuple[Change, ...]:
    raw = _git(repo, ["diff-tree", "--root", "--no-commit-id", "--name-status",
                      "-r", sha]) or ""
    return tuple(_parse_changes(raw))


def _pickaxe_first_source(repo: Path, target: str, by_sha: dict[str, Commit],
                           blob) -> tuple[Commit, str] | None:
    escaped = re.escape(target)
    pattern = rf"(^|[^A-Za-z0-9_]){escaped}[[:space:]]*\("
    raw = _git(repo, ["log", "--all", "--reverse", f"-G{pattern}",
                      "--format=%H", "--", "*.c"]) or ""
    for sha in (line.strip() for line in raw.splitlines() if line.strip()):
        commit = by_sha.get(sha)
        if commit is None:
            # This is unlikely with --all on both queries, but retain an exact
            # source answer if Git ordering/ref visibility differs.
            metadata = _git(repo, ["show", "-s", "--format=%aI%x1f%an%x1f%B", sha])
            assert metadata is not None
            authored_at, author, message = metadata.split(LOG_FIELD, 2)
            commit = Commit(sha, authored_at.strip(), author.strip(), message.strip(),
                            _changes_for_sha(repo, sha))
        for change in commit.changes:
            if not change.path.endswith(".c"):
                continue
            before_path = change.old_path or change.path
            before = extract_function(blob(f"{sha}^", before_path), target)
            after = extract_function(blob(sha, change.path), target)
            if after is not None and before is None:
                return commit, change.path
    return None


def _source_transitions(target: str, events: list[dict[str, object]],
                        by_sha: dict[str, Commit], blob) -> list[dict[str, object]]:
    transitions: list[dict[str, object]] = []
    for event in events:
        commit = by_sha[str(event["sha"])]
        for change in commit.changes:
            if not change.path.endswith(".c"):
                continue
            before_path = change.old_path or change.path
            before = extract_function(blob(f"{commit.sha}^", before_path), target)
            after = extract_function(blob(commit.sha, change.path), target)
            if before is None and after is None:
                continue
            before_text = (before or "") + ("\n" if before else "")
            after_text = (after or "") + ("\n" if after else "")
            if before_text == after_text:
                continue
            diff = "".join(difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"{commit.sha}^:{before_path}",
                tofile=f"{commit.sha}:{change.path}",
            ))
            transitions.append({
                "sha": commit.sha,
                "subject": commit.subject,
                "path": change.path,
                "produced_exactness": bool(event["exact"]),
                "before": before,
                "after": after,
                "diff": diff,
            })
    return transitions

"""Turn shared, measured investigation issues into scoped engineering requests.

This module plans work only. The isolated engineering worker and its independent
reproduction, regression, and transfer gates remain in eval.capability_repair.
"""

from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path

from eval.capability_repair import PROTECTED, validate_task
from solver import repair_queue
from solver.evidence_schedule import fingerprint


_MODEL_FIELDS = {"issue_key", "hypothesis", "modules", "reproduce", "regression", "transfer"}


def _tree(root: Path, path: Path) -> ast.Module | None:
    """Parse a small in-project Python file without exposing its source text."""
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            return None
        raw = path.read_bytes()
        if len(raw) > 512_000 or b"\x00" in raw:
            return None
        return ast.parse(raw.decode("utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, ValueError):
        return None


def catalog(project: Path, query: str, limit: int = 30) -> dict:
    """List only matching in-project API names and existing pytest selectors."""
    if not isinstance(query, str) or len(query) > 120:
        raise ValueError("catalog query must be a string of at most 120 characters")
    if not isinstance(limit, int) or limit < 0:
        raise ValueError("catalog limit must be nonnegative")
    maximum = min(limit, 30)
    root = Path(project).resolve()
    needle = query.casefold()
    modules: list[dict] = []
    tests: list[dict] = []
    for package in ("solver", "oracle"):
        folder = root / package
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.py")):
            if len(modules) >= maximum:
                break
            name = path.relative_to(root).as_posix()
            if name in PROTECTED:
                continue
            tree = _tree(root, path)
            if tree is None:
                continue
            api = [node.name for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            matching = [name for name in api if needle in name.casefold()]
            if needle not in name.casefold() and not matching:
                continue
            modules.append({"path": name, "api": api[:20] if needle in name.casefold()
                            else matching[:20]})
    folder = root / "tests"
    if folder.is_dir():
        for path in sorted(folder.glob("test_*.py")):
            if len(tests) >= maximum:
                break
            name = path.relative_to(root).as_posix()
            tree = _tree(root, path)
            if tree is None:
                continue
            selectors = []
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    selectors.append(name + "::" + node.name)
                elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    selectors.extend(name + "::" + node.name + "::" + method.name
                                     for method in node.body
                                     if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                                     and method.name.startswith("test_"))
            matching = [selector for selector in selectors if needle in selector.casefold()]
            if not matching:
                continue
            tests.append({"path": name, "selectors": matching[:20]})
    return {"modules": modules, "tests": tests}


def _issue(issues: dict, key: str, function: str) -> dict:
    if not isinstance(key, str) or not key:
        raise ValueError("shared issue key must be a nonempty string")
    issue = issues.get(key)
    if not issue:
        raise ValueError("unknown or stale shared issue")
    affected = issue.get("affected_functions") or ()
    if function not in affected:
        raise ValueError("issue is unrelated to this function")
    if len(set(affected)) < 2:
        raise ValueError("single consumer issue is unavailable for automatic engineering")
    if issue.get("key") != key or not isinstance(issue.get("identity"), dict):
        raise ValueError("shared issue has invalid evidence binding")
    return issue


def propose(payload: dict, *, issues: dict, project: Path, function: str) -> dict:
    """Validate a model-authored request and bind it to measured issue identity."""
    if not isinstance(payload, dict):
        raise ValueError("model request must be an object")
    extra = set(payload) - _MODEL_FIELDS
    if extra:
        raise ValueError("model cannot supply evidence, identity, or extra task fields: "
                         + ", ".join(sorted(extra)))
    issue = _issue(issues, payload.get("issue_key"), function)
    hypothesis = payload.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 1200:
        raise ValueError("bounded model hypothesis is required")
    modules = payload.get("modules")
    if not isinstance(modules, list) or not all(isinstance(name, str) for name in modules):
        raise ValueError("implementation modules must be a list of paths")
    for field in ("reproduce", "regression", "transfer"):
        selectors = payload.get(field)
        if not isinstance(selectors, list) or not all(isinstance(selector, str) for selector in selectors):
            raise ValueError(f"{field} must be a list of test selectors")
    task = {field: payload.get(field) for field in _MODEL_FIELDS}
    task.update(evidence=deepcopy(issue["identity"]), model_proposed=True, training_ineligible=True)
    validate_task(task, Path(project))
    return task


def ingest(state: dict, function: str, tasks: list[dict], project: Path) -> list[str]:
    """Recheck current receipts and retain at most one request per shared issue."""
    notes: list[str] = []
    issues = repair_queue.shared_issues(state["nodes"])
    for task in tasks:
        try:
            if not isinstance(task, dict):
                raise ValueError("request is not an object")
            key = task.get("issue_key")
            issue = _issue(issues, key, function)
            if task.get("evidence") != issue["identity"]:
                raise ValueError("request evidence no longer matches current issue")
            if task.get("model_proposed") is not True or task.get("training_ineligible") is not True:
                raise ValueError("request lacks model and training provenance")
            if set(task) != _MODEL_FIELDS | {"evidence", "model_proposed", "training_ineligible"}:
                raise ValueError("request has missing or extra fields")
            validate_task(task, Path(project))
            digest = fingerprint(task)
            existing = state.get("capability_tasks", {}).get(key)
            if existing is not None:
                if fingerprint(existing) == digest:
                    notes.append(f"duplicate capability request for {key}; original budget retained")
                else:
                    notes.append(f"rejected task drift for {key}; original budget retained")
                continue
            attempted = any(
                job.get("profile") == "capability_repair" and job.get("evidence_key") == digest
                for node in state["nodes"].values() for job in node.get("jobs", [])
            )
            if attempted:
                notes.append(f"already attempted capability request for {key}; budget retained")
                continue
            state.setdefault("capability_tasks", {})[key] = deepcopy(task)
            notes.append(f"accepted capability request for {key}")
        except (ValueError, TypeError, KeyError) as exc:
            notes.append(f"unavailable capability request: {exc}")
    return notes


def context(issues: dict, function: str, max_items: int = 6) -> dict:
    """Bounded, read-only descriptors for a model considering a repair request."""
    if max_items < 0:
        raise ValueError("max_items must be nonnegative")
    related = [issue for issue in issues.values()
               if function in (issue.get("affected_functions") or ())]
    related.sort(key=lambda issue: (-len(set(issue.get("affected_functions") or ())), issue.get("key", "")))
    descriptors = []
    for issue in related[:min(max_items, 6)]:
        affected = sorted(set(issue.get("affected_functions") or ()))
        identity = issue.get("identity") or {}
        evidence = (identity if len(json.dumps(identity, sort_keys=True)) <= 2000
                    else {"fingerprint": fingerprint(identity), "omitted": "oversized identity"})
        descriptors.append({"issue_key": issue.get("key"), "kind": issue.get("kind"),
                            "consumer_count": len(affected), "affected_functions": affected[:16],
                            "evidence": evidence,
                            "availability": ("shared engineering request" if len(affected) >= 2
                                             else "unavailable: single consumer")})
    return {"issues": descriptors, "task_schema": {
        "issue_key": "listed shared issue key",
        "hypothesis": "bounded cause and predicted repair effect",
        "modules": "one to three existing solver/oracle Python modules",
        "reproduce": "existing failing pytest selectors",
        "regression": "existing independent regression pytest selectors",
        "transfer": "existing independent transfer pytest selectors",
    }}

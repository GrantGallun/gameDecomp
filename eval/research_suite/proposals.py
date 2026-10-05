"""Bounded, opt-in source hypotheses for research experiments.

These helpers do not compile, score, or certify their output.  The expression
rewrite recognizes one small unsigned, side-effect-free grammar; the type view
recognizes one byte-pointer load grammar.  Unknown C is left alone.
"""

from collections import deque
from itertools import islice
import re


_UINT_MAX = 0xffffffff
_NAME = r"[A-Za-z_]\w*"
_SCOPE = re.compile(rf"^({_NAME}):({_NAME})$")


def compose(source, generate, *, max_depth=2, max_candidates=32) -> list[dict]:
    """Breadth-first composition of source-only generator triples.

    ``path`` records each edit as ``{'label', 'family'}``.  A source is
    expanded once, even when two paths reach the same spelling.  Generator
    results inspected are bounded by the remaining candidate allowance.
    """
    if max_depth <= 0 or max_candidates <= 0:
        return []
    queue = deque([(source, [])])
    seen = {source}
    result = []
    while queue and len(result) < max_candidates:
        parent, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        remaining = max_candidates - len(result)
        for proposal in islice(generate(parent), remaining):
            if not isinstance(proposal, (tuple, list)) or len(proposal) != 3:
                continue
            label, family, child = proposal
            if not all(isinstance(value, str) for value in proposal):
                continue
            if child == parent or child in seen:
                continue
            child_path = path + [{"label": label, "family": family}]
            result.append({"source": child, "label": label, "family": family, "path": child_path})
            seen.add(child)
            queue.append((child, child_path))
            if len(result) >= max_candidates:
                break
    return result


def pure_variants(source: str, function: str):
    """Reassociate then fold small unsigned constants in a single pure return.

    The complete source must be a single ``unsigned int`` function whose sole
    parameter is ``unsigned int``.  Constants must fit without folding
    overflow; no calls, casts, increments, or other expressions are parsed.
    """
    if not re.fullmatch(_NAME, function):
        return []
    head = rf"(?P<head>\s*unsigned\s+int\s+{re.escape(function)}\s*\(\s*unsigned\s+int\s+(?P<var>{_NAME})\s*\)\s*\{{\s*return\s+)"
    tail = r"(?P<tail>\s*;\s*\}\s*)"
    first = re.fullmatch(head + r"\(\s*(?P=var)\s*\+\s*(?P<a>0|[1-9][0-9]*)U\s*\)\s*\+\s*(?P<b>0|[1-9][0-9]*)U" + tail, source)
    if first:
        a, b = int(first["a"]), int(first["b"])
        if a <= _UINT_MAX and b <= _UINT_MAX and a + b <= _UINT_MAX:
            changed = f'{first["head"]}{first["var"]} + ({a}U + {b}U){first["tail"]}'
            return [("reassociate_unsigned_add", "pure_unsigned", changed)]
    second = re.fullmatch(head + r"(?P=var)\s*\+\s*\(\s*(?P<a>0|[1-9][0-9]*)U\s*\+\s*(?P<b>0|[1-9][0-9]*)U\s*\)" + tail, source)
    if second:
        a, b = int(second["a"]), int(second["b"])
        if a <= _UINT_MAX and b <= _UINT_MAX and a + b <= _UINT_MAX:
            changed = f'{second["head"]}{second["var"]} + {a + b}U{second["tail"]}'
            return [("fold_unsigned_constants", "pure_unsigned", changed)]
    return []


def _valid_access(row):
    if not isinstance(row, dict) or not _SCOPE.fullmatch(str(row.get("base", ""))):
        return False
    return (type(row.get("offset")) is int and row["offset"] >= 0
            and type(row.get("width")) is int and row["width"] in (1, 2, 4)
            and type(row.get("signed")) is bool
            and row.get("kind") in ("load", "store")
            and isinstance(row.get("evidence_id"), str) and bool(row["evidence_id"].strip()))


def _valid_flow(row):
    if not isinstance(row, dict):
        return False
    left, right = _SCOPE.fullmatch(str(row.get("from", ""))), _SCOPE.fullmatch(str(row.get("to", "")))
    return (left is not None and right is not None and left[1] == right[1]
            and isinstance(row.get("evidence_id"), str) and bool(row["evidence_id"].strip()))


def infer_views(accesses, flows=()) -> dict:
    """Collect cited scoped accesses and compatible pointer-flow hypotheses.

    A flow edge states that both names carry the same pointer view.  Conflicts
    remain explicit alternatives; no single field is chosen for that offset.
    Invalid or uncited evidence is reported in ``rejected`` and unused.
    """
    accepted = []
    accepted_flows = []
    rejected = []
    for row in accesses:
        if _valid_access(row):
            accepted.append(dict(row))
        else:
            rejected.append({"kind": "access", "row": row, "reason": "invalid_or_uncited"})
    for row in flows:
        if _valid_flow(row):
            accepted_flows.append(dict(row))
        else:
            rejected.append({"kind": "flow", "row": row, "reason": "invalid_or_uncited"})

    graph = {}
    for row in accepted_flows:
        left, right = row["from"], row["to"]
        graph.setdefault(left, []).append((right, row["evidence_id"]))
        graph.setdefault(right, []).append((left, row["evidence_id"]))
    bases = sorted({row["base"] for row in accepted} | set(graph))
    views = []
    for base in bases:
        paths = {base: ()}
        pending = deque([base])
        while pending:
            current = pending.popleft()
            for other, evidence_id in graph.get(current, ()):
                if other not in paths:
                    paths[other] = paths[current] + (evidence_id,)
                    pending.append(other)
        scoped = []
        for row in accepted:
            if row["base"] in paths:
                scoped.append({**row, "evidence_ids": list(dict.fromkeys((row["evidence_id"],) + paths[row["base"]]))})
        fields_by_offset = {}
        for row in scoped:
            key = (row["offset"], row["width"], row["signed"])
            field = fields_by_offset.setdefault(key, {
                "offset": row["offset"], "width": row["width"], "signed": row["signed"],
                "kinds": [], "evidence_ids": [],
            })
            if row["kind"] not in field["kinds"]:
                field["kinds"].append(row["kind"])
            for evidence_id in row["evidence_ids"]:
                if evidence_id not in field["evidence_ids"]:
                    field["evidence_ids"].append(evidence_id)
        fields = sorted(fields_by_offset.values(), key=lambda f: (f["offset"], f["width"], f["signed"]))
        conflicting = set()
        for index, left in enumerate(fields):
            for other_index in range(index + 1, len(fields)):
                right = fields[other_index]
                if (left["offset"] < right["offset"] + right["width"]
                        and right["offset"] < left["offset"] + left["width"]):
                    conflicting.update((index, other_index))
        views.append({
            "base": base,
            "status": "conflict" if conflicting else ("supported" if fields else "unknown"),
            "fields": [f for index, f in enumerate(fields) if index not in conflicting],
            "alternatives": [{"offset": field["offset"], "fields": [field]}
                             for index, field in enumerate(fields) if index in conflicting],
            "accesses": scoped,
        })
    return {"views": views, "flows": accepted_flows, "rejected": rejected}


_C_TYPE = {
    (1, False): "unsigned char", (1, True): "signed char",
    (2, False): "unsigned short", (2, True): "short",
    (4, False): "unsigned int", (4, True): "int",
}


def type_variants(source: str, function: str, report: dict) -> list[dict]:
    """Replace a recognized byte-pointer scalar load with a cited C view.

    The original argument must be exactly ``unsigned char *`` and the whole
    source must be one simple function.  This prevents a stale inference from
    silently retyping a changed input or unrelated function.
    """
    if not re.fullmatch(_NAME, function) or not isinstance(report, dict):
        return []
    pattern = (rf"\s*unsigned\s+int\s+{re.escape(function)}\s*\(\s*unsigned\s+char\s*\*\s*"
               rf"(?P<var>{_NAME})\s*\)\s*\{{\s*return\s+\*\s*\(\s*unsigned\s+int\s*\*\s*\)"
               rf"\s*\(\s*(?P=var)\s*\+\s*(?P<offset>0|[1-9][0-9]*)\s*\)\s*;\s*\}}\s*")
    match = re.fullmatch(pattern, source)
    if not match:
        return []
    base = f'{function}:{match["var"]}'
    view = next((item for item in report.get("views", ()) if item.get("base") == base), None)
    if not view or view.get("status") != "supported" or view.get("alternatives"):
        return []
    fields = view.get("fields", ())
    offset = int(match["offset"])
    if not any(f.get("offset") == offset and f.get("width") == 4 and f.get("signed") is False
               and "load" in f.get("kinds", ()) for f in fields):
        return []
    declarations = []
    cursor = 0
    evidence_ids = []
    for field in sorted(fields, key=lambda f: f["offset"]):
        field_offset, width, signed = field["offset"], field["width"], field["signed"]
        if field_offset < cursor or field_offset % width or (width, signed) not in _C_TYPE:
            return []
        if not field.get("evidence_ids"):
            return []
        if field_offset > cursor:
            declarations.append(f"    unsigned char unknown_{cursor}[{field_offset - cursor}];")
        declarations.append(f"    {_C_TYPE[(width, signed)]} field_{field_offset};")
        cursor = field_offset + width
        evidence_ids.extend(field["evidence_ids"])
    if not declarations:
        return []
    view_name = f"{function}_view"
    struct = "struct " + view_name + " {\n" + "\n".join(declarations) + "\n};\n"
    changed = (struct + f"unsigned int {function}(struct {view_name} *{match['var']}) "
               + "{ return " + f"{match['var']}->field_{offset};" + " }")
    return [{"source": changed, "label": f"evidence_view_{base}_{offset}",
             "family": "type_view", "evidence_ids": list(dict.fromkeys(evidence_ids)),
             "original_param_type": "unsigned char *"}]

"""Deterministic member repair from recorded offset constraints -- no model.

`trace_replay.offset_constraints` reports observables the original reaches at
one offset of an entry pointer and the candidate at another: argument 3 is
a0+0x38 in the game, a0+0x28 in the candidate. With the candidate's
compiler-measured layout (`eval.trace_panel.parameter_fields`) the candidate
offset names the member its C used, and the original offset names the member
that sits where the game reads. Renaming every use of the first to the second,
simultaneously, reproduces the recorded offset without touching the record
declaration.

Declines, with a reason, rather than guessing:
- an offset that is not the exact start of a measured member (array interior,
  padding, or a nested field boundary);
- members of different width or declared type;
- a member the constraints map to two different targets;
- two members mapped onto the same target;
- a member never spelled `param->member` in the function body.

This is inference. The compiler and the recordings decide whether it is right.
Member names are the candidate's own m2c labels, so a rename changes no fact.
"""
from __future__ import annotations

import re

from solver import repair_context


def _exact_member(rows: list[dict], offset: int) -> dict | None:
    starts = [r for r in rows if r.get("offset") == offset and not r.get("array")]
    if not starts:
        return None
    # Innermost (a nested field shares its parent's start): smallest width, longest path.
    return min(starts, key=lambda r: (r.get("width") or 0, -r["member"].count(".")))


def renames(constraints: list[dict], fields: dict) -> tuple[dict[tuple[str, str], str], list[str]]:
    """((parameter, candidate member) -> target member, decline reasons)."""
    mapping: dict[tuple[str, str], str] = {}
    declines: list[str] = []
    conflicted: set[tuple[str, str]] = set()
    for row in constraints:
        field = fields.get(row["register"])
        if not field:
            declines.append(f"{row['where']}: {row['register']} is not a measured record pointer")
            continue
        have = _exact_member(field["rows"], row["candidate_offset"])
        want = _exact_member(field["rows"], row["target_offset"])
        if not have or not want:
            declines.append(f"{row['where']}: offset {row['candidate_offset']:#x} or {row['target_offset']:#x} "
                            "is not the start of a measured member")
            continue
        if (have.get("width"), have.get("canonical")) != (want.get("width"), want.get("canonical")):
            declines.append(f"{row['where']}: {have['member']} and {want['member']} differ in width or type")
            continue
        key = (field["parameter"], have["member"])
        if key in mapping and mapping[key] != want["member"]:
            conflicted.add(key)
        mapping.setdefault(key, want["member"])
    for key in conflicted:
        declines.append(f"{key[0]}->{key[1]} is constrained to more than one member")
        mapping.pop(key)
    targets: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (parameter, member), target in mapping.items():
        targets.setdefault((parameter, target), []).append((parameter, member))
    for (parameter, target), sources in targets.items():
        if len(sources) > 1:
            declines.append(f"{', '.join(m for _p, m in sources)} would all become {parameter}->{target}")
            for source in sources:
                mapping.pop(source, None)
    return {k: v for k, v in mapping.items() if k[1] != v}, declines


def apply(source: str, function: str, mapping: dict[tuple[str, str], str]) -> tuple[str, dict]:
    """Rename `param->member` uses in the function body, all at once."""
    match, end = repair_context.definition(source, function)
    start = match.end()
    body = source[start:end]
    applied, missing = {}, []
    patterns = {}
    for (parameter, member), target in mapping.items():
        pattern = rf"\b{re.escape(parameter)}\s*->\s*{re.escape(member)}\b(?!\s*\.)"
        if not re.search(pattern, body):
            missing.append(f"{parameter}->{member}")
            continue
        patterns[(parameter, member)] = pattern
    if patterns:
        combined = re.compile("|".join(f"(?P<g{i}>{p})" for i, p in enumerate(patterns.values())))
        keys = list(patterns)

        def replace(found):
            index = int(found.lastgroup[1:])
            parameter, member = keys[index]
            applied[f"{parameter}->{member}"] = applied.get(f"{parameter}->{member}", 0) + 1
            return f"{parameter}->{mapping[(parameter, member)]}"

        body = combined.sub(replace, body)
    return source[:start] + body + source[end:], {"applied": applied, "not_in_body": missing}


def propose(source: str, function: str, constraints: list[dict], fields: dict) -> tuple[str | None, dict]:
    """One repaired source, or None, with a report naming every decline."""
    mapping, declines = renames(constraints, fields)
    report = {"kind": "recorded-member-repair", "constraints": len(constraints),
              "renames": {f"{p}->{m}": t for (p, m), t in mapping.items()}, "declines": declines,
              "authority": "inference from recorded offsets and measured candidate layout; compiler decides"}
    if not mapping:
        report["status"] = "declined"
        return None, report
    repaired, applied = apply(source, function, mapping)
    report.update(applied)
    report["status"] = "proposed" if repaired != source else "declined"
    return (repaired if repaired != source else None), report

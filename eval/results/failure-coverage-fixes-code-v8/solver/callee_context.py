"""Compact context from byte-exact direct callees.

This is deliberately an opt-in prompt experiment.  A byte-exact callee gives
its callers useful constraints, but it does not make every spelling in the
matched C semantically unique: ``s32`` and ``u32`` are often ABI-equivalent,
and a plausible local name is not compiler evidence.  Packets therefore keep
three layers separate:

* stable identity and machine observations from the call/evidence tables;
* compiler-compatible source carrying an explicit exact receipt; and
* optional semantic annotations, labelled as fallible hypotheses.

The production solver does not import this module.  ``eval.callee_context_pilot``
uses it for a bounded baseline/facts/semantics comparison before any context is
wired into the main wavefront.
"""

from __future__ import annotations

from collections import defaultdict
import json
import re

from solver import protostore


PARAMETER_ID = re.compile(r"^param[0-3]$")
RETURN = re.compile(r"\breturn(?:\s+([^;]+))?\s*;")
RECOVERY_MARKERS = tuple(protostore.RECOVERY_STRATEGIES)


def _bounded(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, max(0, limit - 80))
    if cut < 0:
        cut = max(0, limit - 80)
    return text[:cut].rstrip() + "\n/* exact callee source truncated */"


def _return_expressions(source: str, limit: int = 3) -> list[str]:
    out = []
    for match in RETURN.finditer(source or ""):
        expression = " ".join((match.group(1) or "void").split())
        if len(expression) > 180:
            expression = expression[:177] + "..."
        if expression not in out:
            out.append(expression)
        if len(out) == limit:
            break
    return out


def _memory_effects(conn, addr: int, limit: int = 14) -> list[str]:
    grouped: dict[tuple, dict[str, int]] = defaultdict(
        lambda: {"reads": 0, "writes": 0})
    rows = conn.execute(
        "select base, offset, width, is_load, op from evidence "
        "where kind = 'mem_access' and func_addr = ? "
        "and width is not null order by base, offset, width", (addr,))
    for base, offset, width, is_load, _op in rows:
        identity = base or "unknown"
        key = (identity, offset, width)
        grouped[key]["reads" if is_load else "writes"] += 1

    effects = []
    for (base, offset, width), counts in sorted(
            grouped.items(), key=lambda item: (
                str(item[0][0]), item[0][1] if item[0][1] is not None else -1,
                item[0][2])):
        location = base
        if offset is not None:
            location += f"+{offset:#x}" if offset >= 0 else f"-{abs(offset):#x}"
        modes = []
        if counts["reads"]:
            modes.append(f"read x{counts['reads']}")
        if counts["writes"]:
            modes.append(f"write x{counts['writes']}")
        effects.append(f"{location}: {width}-byte " + ", ".join(modes))
        if len(effects) == limit:
            break
    return effects


def _calls(conn, addr: int, limit: int = 10) -> tuple[list[str], int]:
    names = [row[0] for row in conn.execute(
        "select distinct tf.name from evidence e "
        "join functions tf on tf.addr = e.target_addr "
        "where e.kind = 'call' and e.func_addr = ? "
        "order by tf.name limit ?", (addr, limit))]
    indirect = conn.execute(
        "select count(*) from evidence where kind = 'call' "
        "and func_addr = ? and target_addr is null", (addr,)).fetchone()[0]
    return names, int(indirect or 0)


def _exact_source(conn, addr: int) -> tuple[dict | None, bool]:
    rows = conn.execute(
        "select id, source_code, coalesce(strategy, '') from attempts "
        "where func_addr = ? and exact = 1 and source_code is not null "
        "order by id desc", (addr,)).fetchall()
    recovered = any(
        marker in strategy
        for _attempt_id, _source, strategy in rows
        for marker in RECOVERY_MARKERS)
    if not rows:
        return None, recovered
    attempt_id, source, strategy = rows[0]
    return {
        "attempt_id": int(attempt_id),
        "strategy": strategy or "unknown-exact-strategy",
        "source": source,
    }, recovered


def packets_for_parent(conn, parent: str, *, include_recovered: bool = False,
                       max_callees: int = 4,
                       max_source_chars: int = 2400) -> list[dict]:
    """Return exact direct-callee packets for ``parent``.

    Recovered target source is excluded by default.  It can be useful in an
    explicitly labelled ceiling experiment, but mixing it into the clean arm
    would attribute handed-over answers to the autonomous system.
    """
    rows = conn.execute(
        "select tf.addr, tf.name, tf.insn_count, count(*) as sites "
        "from evidence e "
        "join functions pf on pf.addr = e.func_addr "
        "join functions tf on tf.addr = e.target_addr "
        "where e.kind = 'call' and pf.name = ? "
        "group by tf.addr, tf.name, tf.insn_count "
        "order by sites desc, tf.insn_count, tf.name", (parent,)).fetchall()

    packets = []
    for addr, name, insn_count, sites in rows:
        exact, recovered = _exact_source(conn, addr)
        if exact is None or (recovered and not include_recovered):
            continue
        source = exact["source"]
        signature = protostore.parse_definition(source, name)
        calls, indirect_calls = _calls(conn, addr)
        packets.append({
            "address": f"0x{int(addr) & 0xFFFFFFFF:08X}",
            "name": name,
            "instruction_count": int(insn_count or 0),
            "parent_call_sites": int(sites),
            "exact_attempt_id": exact["attempt_id"],
            "exact_strategy": exact["strategy"],
            "recovered_from_target_source": recovered,
            "prototype": signature["prototype"] if signature else "",
            "memory_effects": _memory_effects(conn, addr),
            "direct_calls": calls,
            "indirect_call_count": indirect_calls,
            "compatible_return_expressions": _return_expressions(source),
            "exact_source": _bounded(source, max_source_chars),
            "semantic_annotation": None,
        })
        if len(packets) == max_callees:
            break
    return packets


def annotation_prompt(packet: dict) -> str:
    """Ask a model for semantic aliases without presenting them as facts."""
    memory = "\n".join(
        f"- {effect}" for effect in packet["memory_effects"]) or "- none recorded"
    calls = ", ".join(packet["direct_calls"]) or "none"
    returns = " | ".join(
        packet["compatible_return_expressions"]) or "none found"
    return f"""\
You are annotating one byte-exact function for another decompilation agent.

Stable identities are addresses, param0..param3, and explicit offsets. Do not
claim that a proposed name was present in the original source. Infer only what
the supplied calls, accesses, return expressions, and compiler-compatible C
support. Use "unknown" where the evidence is insufficient.

Return JSON only, with this schema:
{{
  "summary": "one short sentence describing observable purpose",
  "parameter_roles": {{"param0": "short role"}},
  "return_role": "short role or unknown",
  "confidence": 0.0,
  "uncertainties": ["short unresolved point"]
}}

THE FUNCTION BODY IS PRESENT BELOW. Analyze it together with these facts:
- stable identity: {packet['address']} {packet['name']}
- compiler-compatible prototype: {packet['prototype'] or 'unknown'}
- resolved calls: {calls}
- unresolved indirect calls: {packet['indirect_call_count']}
- compatible return expression(s): {returns}
- observed memory effects:
{memory}

COMPILER-COMPATIBLE BYTE-EXACT SOURCE:
```c
{packet['exact_source']}
```
"""


def parse_annotation(text: str) -> dict:
    """Parse and bound one fallible semantic annotation."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    if start < 0:
        raise ValueError("annotation response contains no JSON object")
    try:
        value, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid annotation JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("annotation must be a JSON object")

    summary = " ".join(str(value.get("summary") or "").split())[:300]
    if not summary:
        raise ValueError("annotation summary is empty")
    roles = value.get("parameter_roles") or {}
    if not isinstance(roles, dict):
        raise ValueError("parameter_roles must be an object")
    roles = {
        key: " ".join(str(label).split())[:100]
        for key, label in roles.items()
        if PARAMETER_ID.fullmatch(str(key)) and str(label).strip()
    }
    confidence = value.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("annotation confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("annotation confidence must be between 0 and 1")
    uncertainties = value.get("uncertainties") or []
    if not isinstance(uncertainties, list):
        raise ValueError("uncertainties must be an array")
    return {
        "summary": summary,
        "parameter_roles": roles,
        "return_role": " ".join(
            str(value.get("return_role") or "unknown").split())[:140],
        "confidence": confidence,
        "uncertainties": [
            " ".join(str(item).split())[:180]
            for item in uncertainties[:5] if str(item).strip()
        ],
    }


def annotation_is_useful(annotation: dict) -> tuple[bool, str]:
    """Reject abstentions so a vacuous story cannot steer a parent."""
    summary = (annotation.get("summary") or "").lower()
    abstentions = (
        "unable to determine", "cannot determine", "insufficient information",
        "lack of provided", "no function body", "no code provided",
    )
    if any(phrase in summary for phrase in abstentions):
        return False, "semantic model abstained despite supplied callee evidence"
    confidence = annotation.get("confidence")
    if confidence is not None and confidence < 0.2:
        return False, "semantic confidence is below the 0.2 pilot floor"
    return True, ""


def render_packets(packets: list[dict], *, include_source: bool = True,
                   include_semantics: bool = False) -> str:
    """Render bounded direct-callee context for a parent prompt."""
    if not packets:
        return ""
    lines = [
        "\nCOMPLETED DIRECT CALLEES:",
        "These are different functions with explicit byte-exact receipts.",
        "Their C is compiler-compatible, but names and ABI-equivalent types are",
        "not proven original. Stable addresses and observed effects outrank labels.",
    ]
    if include_semantics:
        lines.append(
            "Semantic annotations below are fallible hypotheses, not binary facts.")

    for packet in packets:
        provenance = ("target-source recovery (ceiling context)"
                      if packet["recovered_from_target_source"]
                      else "system exact receipt")
        lines.extend([
            f"\n- {packet['address']} {packet['name']} "
            f"({packet['instruction_count']} instructions; "
            f"{packet['parent_call_sites']} call site(s) in this parent)",
            f"  provenance: {provenance}; attempt "
            f"{packet['exact_attempt_id']}; strategy "
            f"{packet['exact_strategy']}",
        ])
        if packet["prototype"]:
            lines.append(f"  compiler-compatible prototype: {packet['prototype']}")
        if packet["memory_effects"]:
            lines.append("  observed memory effects:")
            lines.extend(f"    * {effect}" for effect in packet["memory_effects"])
        called = list(packet["direct_calls"])
        if called:
            lines.append("  resolved calls: " + ", ".join(called))
        if packet["indirect_call_count"]:
            lines.append(
                f"  unresolved indirect calls: {packet['indirect_call_count']}")
        if packet["compatible_return_expressions"]:
            lines.append(
                "  compatible return expression(s): "
                + " | ".join(packet["compatible_return_expressions"]))

        annotation = packet.get("semantic_annotation")
        if include_semantics and annotation:
            confidence = annotation.get("confidence")
            suffix = (f" (model confidence {confidence:.2f})"
                      if confidence is not None else "")
            lines.append(
                f"  SEMANTIC HYPOTHESIS{suffix}: {annotation['summary']}")
            roles = annotation.get("parameter_roles") or {}
            if roles:
                lines.append("  proposed aliases: " + ", ".join(
                    f"{stable}={label}" for stable, label in sorted(roles.items())))
            lines.append(
                "  proposed return role: "
                + (annotation.get("return_role") or "unknown"))
            for uncertainty in annotation.get("uncertainties") or []:
                lines.append(f"  uncertainty: {uncertainty}")

        if include_source and packet["exact_source"]:
            lines.extend([
                "  compiler-compatible exact body:",
                "```c",
                packet["exact_source"],
                "```",
            ])
    lines.append(
        "\nUse these callees to constrain the parent's calls and value flow; "
        "do not copy unrelated structure.\n")
    return "\n".join(lines)

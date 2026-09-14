"""Interprocedural contracts from completed callees to unresolved callers.

Whole callee bodies are poor parent context: most implementation details cannot
affect caller code generation.  The useful information is the ABI/effect
contract bound to each concrete callsite.  This module combines three layers
without conflating their authority:

* caller argument and return-use identities from fixed-point binary dataflow;
* compiler-compatible prototypes and callee effects from frozen exact nodes;
* optional semantic aliases, explicitly labelled as hypotheses.

Stable identities are addresses, instruction indices, ``param0..param3``, and
``ret(callee@site)``.  No original variable name is claimed.
"""

from __future__ import annotations

from collections import Counter
import re
import sqlite3
from typing import Iterable

from solver import cfg, dataflow, protostore


SCHEMA_VERSION = 1
RECOVERY_MARKERS = tuple(protostore.RECOVERY_STRATEGIES)
REGISTER_NAMES = frozenset({
    "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
    "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9",
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8",
    "gp", "sp", "fp", "ra", "f0", "f2", "f4", "f6", "f8", "f10",
    "f12", "f14", "f16", "f18", "f20", "f22", "f24", "f26", "f28",
    "f30",
})
REGISTER = re.compile(r"\$?([A-Za-z][A-Za-z0-9]*)")
NUMERIC_BRANCH_TARGET = re.compile(r"^(?:0x)?[0-9A-Fa-f]+$")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(
            f"pragma table_info({table})")}
    except sqlite3.Error:
        return set()


def _split_params(text: str) -> list[str]:
    text = (text or "").strip()
    if not text or text == "void":
        return []
    out, current, depth = [], [], 0
    for char in text:
        if char == "," and depth == 0:
            out.append("".join(current).strip())
            current = []
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        current.append(char)
    if current:
        out.append("".join(current).strip())
    return [item for item in out if item and item != "void"]


def _type_contract(spelling: str) -> dict[str, object]:
    normalized = " ".join((spelling or "unknown").replace(" *", "*").split())
    low = normalized.lower()
    pointer = "*" in normalized or "[" in normalized
    if low == "void":
        cls, width, signed = "void", 0, None
    elif pointer:
        cls, width, signed = "pointer", 4, False
    elif any(token in low for token in ("f64", "double")):
        cls, width, signed = "float", 8, None
    elif any(token in low for token in ("f32", "float")):
        cls, width, signed = "float", 4, None
    else:
        width = next((size for token, size in (
            ("s8", 1), ("u8", 1), ("char", 1),
            ("s16", 2), ("u16", 2), ("short", 2),
            ("s64", 8), ("u64", 8), ("long long", 8),
            ("s32", 4), ("u32", 4), ("int", 4), ("long", 4),
        ) if token in low), None)
        cls = "integer" if width is not None else "unknown"
        signed = (False if re.search(r"\b(?:u8|u16|u32|u64|unsigned)\b", low)
                  else True if re.search(
                      r"\b(?:s8|s16|s32|s64|signed|int|short|long)\b", low)
                  else None)
    return {
        "spelling": normalized,
        "class": cls,
        "width": width,
        "signed": signed,
        "pointer": pointer,
        "authority": "compiler-compatible exact-source ABI spelling",
    }


def _parameter_contract(declaration: str, index: int) -> dict[str, object]:
    declaration = " ".join(declaration.split())
    # Function-pointer declarations are kept verbatim; inventing a clean type
    # from them would be worse than admitting that the spelling is complex.
    if "(*" in declaration:
        type_spelling, compatible_name = declaration, ""
    else:
        match = re.search(r"([A-Za-z_]\w*)(?:\s*\[[^]]*\])?\s*$", declaration)
        compatible_name = match.group(1) if match else ""
        type_spelling = declaration[:match.start()].strip() if match else declaration
        if not type_spelling:
            type_spelling = declaration
    return {
        "stable_id": f"param{index}",
        "compatible_name": compatible_name,
        **_type_contract(type_spelling),
    }


def contract_for_node(name: str, node: dict[str, object],
                      annotation: dict | None = None) -> dict[str, object] | None:
    """Build one bounded contract from a frozen exact-function node."""
    trust = node.get("trust")
    source = node.get("exact_source")
    if not isinstance(trust, dict) or trust.get("exact") is not True \
            or not isinstance(source, str):
        return None
    strategy = str(trust.get("strategy") or "")
    if any(marker in strategy for marker in RECOVERY_MARKERS):
        return None
    signature = protostore.parse_definition(source, name)
    if signature is None:
        return None
    params = [_parameter_contract(item, index) for index, item in enumerate(
        _split_params(str(signature["params"]))[:4])]
    machine = node.get("machine") if isinstance(node.get("machine"), dict) else {}
    profile = (node.get("source_profile")
               if isinstance(node.get("source_profile"), dict) else {})
    result: dict[str, object] = {
        "callee": name,
        "prototype": signature["prototype"],
        "parameters": params,
        "return": _type_contract(str(signature["ret"])),
        "effects": list(machine.get("memory_shapes", []))[:32],
        "direct_calls": list(machine.get("direct_calls", []))[:16],
        "indirect_call_count": int(machine.get("indirect_call_count", 0) or 0),
        "compatible_return_expressions": _return_expressions(source),
        "trust": {
            "exact": True,
            "attempt_id": trust.get("attempt_id"),
            "strategy": strategy or "unknown-exact-strategy",
            "authority": trust.get(
                "authority", "target compiler plus byte-exact object oracle"),
        },
        "source_profile": {
            "call_sequence": list(profile.get("call_sequence", []))[:16],
            "control": dict(profile.get("control", {})),
        },
    }
    if annotation:
        result["semantic_hypothesis"] = annotation
    return result


def _return_expressions(source: str, limit: int = 3) -> list[str]:
    out = []
    for match in re.finditer(r"\breturn(?:\s+([^;]+))?\s*;", source or ""):
        expression = " ".join((match.group(1) or "void").split())
        if len(expression) > 180:
            expression = expression[:177] + "..."
        if expression not in out:
            out.append(expression)
        if len(out) == limit:
            break
    return out


def _evidence_calls(conn: sqlite3.Connection, parent: str) -> list[dict[str, object]]:
    ecols, fcols = _columns(conn, "evidence"), _columns(conn, "functions")
    if not {"kind", "func_addr", "target_addr"} <= ecols \
            or not {"addr", "name"} <= fcols:
        return []
    address = "e.addr" if "addr" in ecols else "e.rowid"
    opcode = "e.op" if "op" in ecols else "'call'"
    rows = conn.execute(
        f"select {address}, {opcode}, e.target_addr, tf.name "
        "from evidence e join functions pf on pf.addr=e.func_addr "
        "left join functions tf on tf.addr=e.target_addr "
        "where e.kind='call' and pf.name=? order by " + address,
        (parent,)).fetchall()
    return [{
        "pc": int(pc) if "addr" in ecols and pc is not None else None,
        "opcode": str(op or "call"),
        "target_addr": int(target_addr) if target_addr is not None else None,
        "target": str(target) if target is not None else None,
    } for pc, op, target_addr, target in rows]


def _align_callsites(callsites: Iterable[dataflow.CallSite],
                     evidence: list[dict[str, object]]) -> list[tuple[
                         dataflow.CallSite, dict[str, object] | None, bool]]:
    ordered = sorted(callsites, key=lambda item: item.instruction)
    if len(ordered) != len(evidence):
        return [(call, None, False) for call in ordered]
    out = []
    for call, row in zip(ordered, evidence):
        same = (not call.target or not row.get("target")
                or call.target == row.get("target"))
        out.append((call, row if same else None, bool(same)))
    return out


def _registers(operands: Iterable[str]) -> set[str]:
    found = set()
    for operand in operands:
        for match in REGISTER.finditer(operand):
            name = match.group(1).lower()
            if name in REGISTER_NAMES:
                found.add(name)
    return found


def _values_from_call(state: dataflow.State, insn: cfg.Instruction,
                      site: int) -> list[str]:
    out = []
    for register in sorted(_registers(insn.operands)):
        value = state.registers.get(register)
        if value is not None and value.comes_from_call(site):
            out.append(f"{register}={value.describe(precise=True)}")
    return out


def _return_uses(result: dataflow.Result,
                 call: dataflow.CallSite) -> list[dict[str, object]]:
    uses = []
    after = call.delay_slot if call.delay_slot is not None else call.instruction
    for insn in result.graph.instructions:
        if insn.index <= after:
            continue
        state = result.instruction_in.get(insn.index)
        if state is None:
            continue
        values = _values_from_call(state, insn, call.instruction)
        access = result.accesses.get(insn.index)
        kinds: list[tuple[str, str]] = []
        if access is not None and access.address is not None \
                and access.address.comes_from_call(call.instruction):
            kinds.append((
                "return_used_as_memory_address",
                f"{'read from' if access.is_load else 'write through'} "
                f"{access.address.describe(precise=True)}",
            ))
        if insn.opcode in dataflow.STORE_WIDTH and insn.operands:
            source = dataflow.reg(insn.operands[0])
            value = state.registers.get(source)
            if value is not None and value.comes_from_call(call.instruction):
                destination = (access.address.describe(precise=True)
                               if access is not None and access.address is not None
                               else "unresolved memory")
                kinds.append(("return_value_stored",
                              f"{value.describe(precise=True)} stored at {destination}"))
        nested = result.callsites.get(insn.index)
        if nested is not None:
            bindings = [
                f"param{index} of {nested.target or 'indirect call'} <- "
                f"{value.describe(precise=True)}"
                for index, value in enumerate(nested.arguments)
                if value is not None and value.comes_from_call(call.instruction)
            ]
            if bindings:
                kinds.append(("return_passed_to_call", "; ".join(bindings)))
        if cfg.is_conditional_branch(insn.opcode) and values:
            kinds.append(("return_controls_branch", insn.text))
        if insn.opcode == "jr" and insn.operands \
                and dataflow.reg(insn.operands[0]) == "ra":
            returned = state.registers.get("v0")
            if returned is not None and returned.comes_from_call(call.instruction):
                kinds.append(("return_forwarded_by_parent",
                              returned.describe(precise=True)))
        if values and not kinds and insn.opcode not in {"nop", "jr"}:
            kinds.append(("return_transformed_or_copied", insn.text))
        for kind, detail in kinds:
            uses.append({
                "instruction_index": insn.index,
                "kind": kind,
                "detail": detail,
                "instruction": insn.text,
                "symbolic_values": values,
            })
    unique = {}
    for use in uses:
        unique[(use["instruction_index"], use["kind"])] = use
    return list(unique.values())[:24]


def _bind_effects(effects: Iterable[str], arguments: list[dict[str, object]]) \
        -> list[str]:
    values = {str(arg["stable_id"]): str(arg["value"])
              for arg in arguments if arg.get("resolved")}
    out = []
    for effect in effects:
        match = re.match(r"^(param[0-3])(@.*)$", str(effect))
        if match and match.group(1) in values:
            out.append(values[match.group(1)] + match.group(2))
        else:
            out.append(str(effect))
    return out


def _binary_value(value: dataflow.Value | None) -> dict[str, object]:
    """Keep machine facts structured instead of asking prose to encode types."""
    if value is None:
        return {"kind": "unknown"}
    if value.kind == "load" and value.inner is not None:
        address = value.inner
        out: dict[str, object] = {
            "kind": "memory_load",
            "width": value.width,
            "signed": value.signed,
            "post_load_byte_offset": value.offset,
            "expression": value.describe(precise=True),
        }
        if address.kind == "address":
            out.update({
                "base": address.name,
                "byte_offset": address.offset,
                "base_kind": ("caller_identity"
                              if address.name.startswith("param")
                              or address.name in {"stack", "gp"}
                              else "linker_identity"),
            })
            if value.width and address.offset >= 0 \
                    and address.offset % value.width == 0:
                out["compatible_element_index"] = address.offset // value.width
        return out
    if value.kind == "address":
        return {
            "kind": "address",
            "base": value.name,
            "byte_offset": value.offset,
            "expression": value.describe(precise=True),
        }
    if value.kind == "constant":
        return {"kind": "constant", "value": value.offset,
                "expression": value.describe(precise=True)}
    if value.kind == "call_result":
        return {"kind": "call_result", "callee": value.name,
                "site": value.site, "byte_offset": value.offset,
                "expression": value.describe(precise=True)}
    return {"kind": value.kind, "expression": value.describe(precise=True)}


def _render_binary_value(argument: dict[str, object]) -> str:
    value = argument.get("binary_value")
    if not isinstance(value, dict):
        return str(argument.get("value") or "unknown")
    if value.get("kind") != "memory_load":
        return str(value.get("expression") or "unknown")
    width = value.get("width")
    signed = value.get("signed")
    signedness = ("signed" if signed is True else
                  "unsigned" if signed is False else "raw")
    base = value.get("base")
    offset = value.get("byte_offset")
    if base is None or offset is None or width is None:
        return str(value.get("expression") or "unresolved memory load")
    phrase = (f"{signedness} {int(width) * 8}-bit load from "
              f"{value.get('base_kind', 'binary')} {base} at byte offset "
              f"{int(offset):#x}")
    index = value.get("compatible_element_index")
    if index is not None:
        phrase += (f"; compatible {int(width)}-byte element index "
                   f"{int(index):#x}")
    post_offset = int(value.get("post_load_byte_offset") or 0)
    if post_offset:
        phrase += f"; then add {post_offset:#x} bytes"
    return phrase


def _comparable_binary_value(value: dict[str, object]) -> dict[str, object]:
    """Return only binary facts that must survive compatible C spellings."""
    kind = value.get("kind")
    keys = {
        "memory_load": (
            "kind", "width", "signed", "base", "byte_offset",
            "post_load_byte_offset",
        ),
        "address": ("kind", "base", "byte_offset"),
        "constant": ("kind", "value"),
        "call_result": ("kind", "callee", "byte_offset"),
    }.get(str(kind), ("kind", "expression"))
    return {key: value.get(key) for key in keys}


def _resolve_numeric_branch_targets(asm: str) -> str:
    """Give normalized objdump byte targets labels understood by the CFG."""
    instructions, _labels = cfg.parse_assembly(asm)
    targets: dict[int, str] = {}
    replacements: dict[int, str] = {}
    for insn in instructions:
        target = insn.target
        if target is None or not NUMERIC_BRANCH_TARGET.fullmatch(target):
            continue
        byte_offset = int(target, 16)
        instruction = byte_offset // 4
        if byte_offset % 4 or not 0 <= instruction < len(instructions):
            continue
        label = f".Lcontract_{byte_offset:X}"
        targets[instruction] = label
        replacements[insn.index] = label
    if not replacements:
        return asm
    lines = []
    for insn in instructions:
        for label in insn.labels:
            lines.append(f"{label}:")
        if insn.index in targets:
            lines.append(f"{targets[insn.index]}:")
        operands = list(insn.operands)
        if insn.index in replacements:
            operands[-1] = replacements[insn.index]
        lines.append(insn.opcode + (" " + ", ".join(operands)
                                    if operands else ""))
    return "\n".join(lines)


def _return_use_fact(use: dict[str, object]) -> tuple[str, str, int | None]:
    """Normalize a return consumer without depending on register allocation."""
    kind = str(use.get("kind") or "unknown")
    instruction = str(use.get("instruction") or "").strip()
    opcode = instruction.split(None, 1)[0].lower() if instruction else ""
    operands = instruction.split(None, 1)[1] if " " in instruction else ""
    if kind == "return_transformed_or_copied" and (
            opcode == "move" or
            (opcode in {"or", "addu"} and "$zero" in operands)):
        opcode = "copy"
    operand = ""
    if kind == "return_passed_to_call" and opcode in {"jal", "jalr"}:
        operand = instruction.split(None, 1)[1].split(",", 1)[0].strip() \
            if " " in instruction else ""
    elif kind in {"return_value_stored", "return_used_as_memory_address"}:
        # Width/signedness live in the opcode; the byte offset is stable while
        # the chosen base register is not.
        match = re.search(r",\s*(-?(?:0x)?[0-9A-Fa-f]+)\s*\(", instruction)
        if match:
            token = match.group(1).lower()
            try:
                operand = str(int(token, 0))
            except ValueError:
                operand = str(int(token, 16))
    # Count repeated equivalent consumers (for example two stores at +0x4).
    return kind, f"{opcode}:{operand}", None


def _return_use_counter(uses: Iterable[dict[str, object]]) -> Counter:
    return Counter(_return_use_fact(use) for use in uses)


def validate_candidate_asm(bundle: dict[str, object],
                           candidate_asm: str) -> dict[str, object]:
    """Check compiled parent call arguments and return flow against leaf facts.

    This is intentionally assembly-to-assembly.  It does not care which C
    variable names or casts produced an argument, only whether the compiled
    callsite retained the target load width, signedness, address, and offset.
    """
    expected = [row for row in bundle.get("callsites", [])
                if row.get("exact_callee_contract")]
    observed = dataflow.analyse(_resolve_numeric_branch_targets(candidate_asm))
    by_target: dict[str, list[dataflow.CallSite]] = {}
    for call in sorted(observed.callsites.values(),
                       key=lambda item: item.instruction):
        if call.target:
            by_target.setdefault(call.target, []).append(call)

    target_occurrence: dict[str, int] = {}
    checks: list[dict[str, object]] = []
    mismatches: list[dict[str, object]] = []
    for row in expected:
        callee = str(row["exact_callee_contract"]["callee"])
        occurrence = target_occurrence.get(callee, 0)
        target_occurrence[callee] = occurrence + 1
        candidates = by_target.get(callee, [])
        if occurrence >= len(candidates):
            mismatch = {
                "kind": "missing_call",
                "callee": callee,
                "occurrence": occurrence,
            }
            checks.append({**mismatch, "matches": False})
            mismatches.append(mismatch)
            continue
        call = candidates[occurrence]
        for index, argument in enumerate(row.get("arguments", [])):
            expected_value = argument.get("binary_value")
            if not isinstance(expected_value, dict):
                continue
            observed_value = _binary_value(call.arguments[index])
            expected_fact = _comparable_binary_value(expected_value)
            observed_fact = _comparable_binary_value(observed_value)
            matches = expected_fact == observed_fact
            check = {
                "kind": "argument",
                "callee": callee,
                "occurrence": occurrence,
                "parameter": index,
                "expected": expected_fact,
                "observed": observed_fact,
                "matches": matches,
            }
            checks.append(check)
            if not matches:
                mismatches.append({key: value for key, value in check.items()
                                   if key != "matches"})

        contract = row["exact_callee_contract"]
        is_void = contract.get("return", {}).get("class") == "void"
        expected_uses = _return_use_counter(row.get("return_uses", []))
        observed_uses = _return_use_counter(
            [] if is_void else _return_uses(observed, call))
        for fact in sorted(set(expected_uses) | set(observed_uses)):
            expected_count = expected_uses[fact]
            observed_count = observed_uses[fact]
            matches = expected_count == observed_count
            check = {
                "kind": "return_use",
                "callee": callee,
                "occurrence": occurrence,
                "use": {"kind": fact[0], "shape": fact[1]},
                "expected_count": expected_count,
                "observed_count": observed_count,
                "matches": matches,
            }
            checks.append(check)
            if not matches:
                mismatches.append({key: value for key, value in check.items()
                                   if key != "matches"})

    expected_counts = {callee: count for callee, count in target_occurrence.items()}
    for callee, count in expected_counts.items():
        observed_count = len(by_target.get(callee, []))
        if observed_count > count:
            mismatch = {
                "kind": "extra_calls",
                "callee": callee,
                "expected": count,
                "observed": observed_count,
            }
            checks.append({**mismatch, "matches": False})
            mismatches.append(mismatch)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "exact_leaf_candidate_validation",
        "parent": bundle.get("parent"),
        "exact_leaf_calls": len(expected),
        "argument_checks": sum(row["kind"] == "argument" for row in checks),
        "return_use_checks": sum(
            row["kind"] == "return_use" for row in checks),
        "passed": not mismatches,
        "checks": checks,
        "mismatches": mismatches,
    }


def build(conn: sqlite3.Connection, parent: str, asm: str,
          library: dict[str, object], *,
          annotations: dict[str, dict] | None = None) -> dict[str, object]:
    """Build callsite-bound contracts for one parent function."""
    analysis = dataflow.analyse(asm)
    evidence = _evidence_calls(conn, parent)
    nodes = library.get("nodes") if isinstance(library.get("nodes"), dict) else {}
    rows = []
    for call, observed, aligned in _align_callsites(
            analysis.callsites.values(), evidence):
        target = (str(observed["target"]) if observed and observed.get("target")
                  else call.target)
        node = nodes.get(target) if target else None
        contract = (contract_for_node(
            target, node, (annotations or {}).get(target))
            if target and isinstance(node, dict) else None)
        arity = len(contract["parameters"]) if contract else 4
        arguments = []
        for index in range(arity):
            value = call.arguments[index]
            param = (contract["parameters"][index] if contract else {
                "stable_id": f"param{index}", "class": "unknown",
                "width": None, "signed": None, "pointer": False,
                "spelling": "unknown",
            })
            arguments.append({
                **param,
                "value": (value.describe(precise=True)
                          if value is not None else "unknown"),
                "binary_value": _binary_value(value),
                "resolved": value is not None,
                "authority": "caller binary dataflow",
            })
        traced_uses = _return_uses(analysis, call)
        # Intraprocedural MIPS dataflow cannot know whether v0 is defined by a
        # call.  The exact ABI contract can: a void callee has no return value,
        # so a later stale v0 identity is an analysis artifact, not evidence.
        is_void = bool(contract and contract["return"].get("class") == "void")
        uses = [] if is_void else traced_uses
        rows.append({
            "instruction_index": call.instruction,
            "pc": (f"0x{int(observed['pc']) & 0xFFFFFFFF:08X}"
                   if observed and observed.get("pc") is not None else None),
            "opcode": call.opcode,
            "callee": target,
            "resolved_target": bool(target),
            "evidence_aligned": aligned,
            "arguments": arguments,
            "argument_resolution": {
                "resolved": sum(bool(arg["resolved"]) for arg in arguments),
                "total": len(arguments),
            },
            "exact_callee_contract": contract,
            "bound_effects": _bind_effects(
                contract.get("effects", []) if contract else [], arguments),
            "return_uses": uses,
            "return_consumed": bool(uses),
            "discarded_void_result_traces": len(traced_uses) if is_void else 0,
        })

    exact = [row for row in rows if row["exact_callee_contract"]]
    resolved_args = sum(row["argument_resolution"]["resolved"] for row in exact)
    total_args = sum(row["argument_resolution"]["total"] for row in exact)
    parent_row = conn.execute(
        "select addr from functions where name=? limit 1", (parent,)).fetchone()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "interprocedural_callsite_contracts",
        "parent": parent,
        "parent_address": (f"0x{int(parent_row[0]) & 0xFFFFFFFF:08X}"
                           if parent_row else None),
        "library_digest": library.get("digest"),
        "callsites": rows,
        "coverage": {
            "binary_calls": len(rows),
            "evidence_calls": len(evidence),
            "aligned_calls": sum(bool(row["evidence_aligned"]) for row in rows),
            "resolved_targets": sum(bool(row["resolved_target"]) for row in rows),
            "exact_callee_contracts": len(exact),
            "resolved_exact_arguments": resolved_args,
            "total_exact_arguments": total_args,
            "exact_calls_with_consumed_return": sum(
                bool(row["return_consumed"]) for row in exact),
        },
        "authority": {
            "callsite_bindings": "caller binary fixed-point dataflow",
            "callee_abi": "compiler-compatible source with byte-exact receipt",
            "semantics": "fallible hypotheses when present",
        },
    }


def render(bundle: dict[str, object], *, max_calls: int = 12) -> str:
    """Render compact constraints; never include a completed function body."""
    exact = [row for row in bundle.get("callsites", [])
             if row.get("exact_callee_contract")]
    if not exact:
        return ""
    lines = [
        "\nINTERPROCEDURAL CALLSITE CONTRACTS:",
        "Argument bindings and return uses are binary-derived facts. Prototypes",
        "are compiler-compatible byte-exact spellings, not proof of original",
        "names or ABI-equivalent types. Typed loads preserve binary width and",
        "signedness; symbol offsets are bytes, not C array indexes. Symbol names",
        "are linker identities: use project declarations, never invent an extern",
        "type from an address expression. Semantic labels are hypotheses.",
    ]
    for row in exact[:max_calls]:
        contract = row["exact_callee_contract"]
        where = row.get("pc") or f"instruction {row['instruction_index']}"
        return_type = contract["return"]
        lines.extend([
            f"\n- {where}: call {contract['callee']}",
            f"  compatible prototype: {contract['prototype']}",
        ])
        for argument in row["arguments"]:
            lines.append(
                f"  {argument['stable_id']} ({argument['spelling']}) <- "
                f"{_render_binary_value(argument)}")
        relevant_effects = [effect for effect in row.get("bound_effects") or []
                            if str(effect).startswith("param")
                            or str(effect).endswith(":write")]
        if relevant_effects:
            lines.append("  callee effects bound into caller identities: "
                         + "; ".join(relevant_effects[:8]))
        if return_type.get("class") != "void":
            lines.append(
                f"  result ret@{where}: {return_type['spelling']} "
                f"({return_type['class']}, {return_type.get('width')} byte)")
            if row.get("return_uses"):
                lines.append("  caller return flow:")
                lines.extend(
                    f"    * {use['kind']}: {use['detail']}"
                    for use in row["return_uses"][:8])
            else:
                lines.append("  caller has no mechanically resolved return use")
        semantic = contract.get("semantic_hypothesis")
        if semantic:
            lines.append("  semantic hypothesis: "
                         + str(semantic.get("summary") or "unknown"))
    lines.extend([
        "\nUse these constraints for declarations, argument expressions, and",
        "return-value types. Do not reproduce callee implementations.\n",
    ])
    return "\n".join(lines)

"""Encoded-address polling and uncached word-access candidates; no device model.

Only a closed empty polling loop and an OR-tagged word access are supported.
Instruction words supply addresses. Source names never supply numeric values.
The compiler/frontend/object or ROM-backed certificate still decides success.
"""
import hashlib
import re
from dataclasses import replace
from solver import cfg, dataflow, hardware_environment, project_headers, repair_context

REGISTERS = "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split()


def _reg(text):
    text = dataflow.reg(text)
    if text == "s8":
        text = "fp"
    return REGISTERS.index(text) if text in REGISTERS else int(text) if text.isdigit() else -1


def _encoded(assembly):
    rows = []
    labels = []
    for line in assembly.splitlines():
        match = re.match(r"\s*/\*\s*[\da-fA-F]+\s+[\da-fA-F]+\s+([\da-fA-F]{8})\s*\*/\s*(.*)$", line)
        if match:
            instructions, _ = cfg.parse_assembly(match[2])
            if len(instructions) != 1:
                return []
            rows.append((replace(instructions[0], labels=tuple(labels) + instructions[0].labels), int(match[1], 16)))
            labels = []
        else:
            clean = cfg.clean_line(line)
            if clean and not clean.startswith("glabel ") and not clean.endswith(":"):
                return []
            if clean.endswith(":"):
                labels.append(clean[:-1])
    return rows


def _lui_register(ins, word, symbol):
    if (ins.opcode == "lui" and len(ins.operands) == 2
            and ins.operands[1] == f"%hi({symbol})" and word >> 26 == 15
            and not (word >> 21) & 31
            and _reg(ins.operands[0]) == (word >> 16) & 31):
        return (word >> 16) & 31
    return 0


def _preserves(ins, word, register):
    """Only decoded, straight-line operations can carry a witnessed value."""
    if ins.labels:
        return False
    if ins.opcode == "nop":
        return word == 0 and not ins.operands
    if ins.opcode == "or" and len(ins.operands) == 3:
        return (word >> 26 == 0 and word & 2047 == 37
                and [_reg(o) for o in ins.operands] == [(word >> 11) & 31, (word >> 21) & 31, (word >> 16) & 31]
                and (word >> 11) & 31 != register)
    if ins.opcode in {"addiu", "andi"} and len(ins.operands) == 3:
        immediate = word & 65535
        if ins.opcode == "addiu" and immediate & 32768:
            immediate -= 65536
        try:
            return (word >> 26 == {"addiu": 9, "andi": 12}[ins.opcode]
                    and [_reg(o) for o in ins.operands[:2]] == [(word >> 16) & 31, (word >> 21) & 31]
                    and int(ins.operands[2], 0) == immediate and (word >> 16) & 31 != register)
        except ValueError:
            pass
    # Calls, control transfers, unknown encodings and contradictory text decline.
    return False


def _poll_masks(rows, symbol, mask):
    witnesses = []
    for index, (ins, word) in enumerate(rows):
        if (not index or ins.labels or ins.opcode != "lw" or len(ins.operands) != 2
                or f"%lo({symbol})" not in ins.text or word >> 26 != 35):
            continue
        loaded = (word >> 16) & 31
        seed, seed_word = rows[index - 1]
        base = _lui_register(seed, seed_word, symbol)
        operand = re.fullmatch(r"%lo\(\w+\)\(\$?(\w+)\)", ins.operands[1])
        if (not loaded or not base or not operand or _reg(operand[1]) != base
                or (word >> 21) & 31 != base or _reg(ins.operands[0]) != loaded):
            continue
        for following, bits in rows[index + 1:index + 4]:
            if (not following.labels and following.opcode == "andi" and len(following.operands) == 3
                    and bits >> 26 == 12 and (bits & 65535) == mask
                    and (bits >> 21) & 31 == loaded
                    and (bits >> 16) & 31 != 0
                    and _reg(following.operands[0]) == (bits >> 16) & 31
                    and _reg(following.operands[1]) == loaded
                    and int(following.operands[2], 0) == mask):
                witnesses.append(f"{bits:08x}")
                break
            if not _preserves(following, bits, loaded):
                break
    return witnesses


def _tags(rows):
    """Closed LUI -> OR -> zero-offset word access, with register clobber checks."""
    found = {}
    for index, (memory, bits) in enumerate(rows):
        if index < 2 or memory.labels or memory.opcode not in {"lw", "sw"} or len(memory.operands) != 2:
            continue
        operand = re.fullmatch(r"%lo\((\w+)\)\(\$?(\w+)\)", memory.operands[1])
        if (not operand or bits >> 26 != {"lw": 35, "sw": 43}[memory.opcode]
                or bits & 65535 or _reg(memory.operands[0]) != (bits >> 16) & 31
                or _reg(operand[2]) != (bits >> 21) & 31):
            continue
        combine, word = rows[index - 1]
        if (combine.labels or combine.opcode != "or" or len(combine.operands) != 3 or word >> 26 or word & 2047 != 37
                or [_reg(o) for o in combine.operands] != [(word >> 11) & 31, (word >> 21) & 31, (word >> 16) & 31]
                or not (word >> 11) & 31
                or _reg(combine.operands[0]) != _reg(operand[2])):
            continue
        for earlier in range(index - 2, max(-1, index - 9), -1):
            seed, value = rows[earlier]
            register = _lui_register(seed, value, operand[1])
            if not register or register not in [_reg(o) for o in combine.operands[1:]]:
                continue
            if not all(_preserves(i, bits, register) for i, bits in rows[earlier + 1:index - 1]):
                continue
            address = (value & 65535) << 16
            if 0xA0000000 <= address < 0xC0000000:
                found.setdefault(operand[1], []).append(address)
    return {name: values[0] for name, values in found.items() if len(set(values)) == 1}


def propose(source, function, assembly, *, big_endian_o32=False):
    report = {"source": source, "changes": [], "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "assembly_sha256": hashlib.sha256(assembly.encode()).hexdigest(),
              "authority": "encoded MMIO source candidate; device behavior remains unmodeled"}
    if not big_endian_o32:
        return report
    definition, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    body = masked[definition.end():end - 1]
    rows = _encoded(assembly)
    if not rows or re.search(r"\b(?:goto|asm|__asm__)\b|^[ \t]*#", body, re.M):
        return report
    poll = re.search(r"if\s*\(\s*(?P<reg>\w+_REG)\s*&\s*(?P<mask>0x[\da-fA-F]+|\d+)\s*\)\s*\{\s*"
                     r"do\s*\{\s*\}\s*while\s*\(\s*(?P=reg)\s*&\s*(?P=mask)\s*\)\s*;\s*\}", body)
    if (not poll or len(re.findall(r"\b" + poll["reg"] + r"\b", body)) != 2
            or re.search(r"\b" + poll["reg"] + r"\b", definition[2])):
        return report
    registers = hardware_environment.encoded_register_addresses(assembly)
    evidence = registers.get(poll["reg"], [])
    mask = int(poll["mask"], 0)
    masks = _poll_masks(rows, poll["reg"], mask)
    if len(evidence) != 2 or len(masks) != 2 or not 0 < mask <= 65535:
        return report
    address = evidence[0]["address"]
    if not 0xA4000000 <= address < 0xA5000000 or address % 4:
        return report
    tags = _tags(rows)
    rewritten = source[definition.end():end - 1]
    tag_changes = []
    for symbol, value in tags.items():
        if re.search(r"\b" + re.escape(symbol) + r"\b", definition[2]):
            continue
        token = re.compile(r"\(\s*(?:s32|u32)\s*\)\s*&\s*" + re.escape(symbol) + r"\b")
        matches = list(token.finditer(body))
        if (len(matches) != 1 or matches[0].start() < poll.end()
                or len(re.findall(r"\b" + re.escape(symbol) + r"\b", body)) != 1):
            continue
        literal = f"0x{value:08X}U"
        match = matches[0]
        changed = rewritten[:match.start()] + literal + rewritten[match.end():]
        changed_mask = project_headers._mask_noncode(changed)
        # Only the typed word access containing the witnessed OR tag becomes volatile.
        accesses = []
        for access in re.finditer(r"\*\(\s*(?:s32|u32)\s*\*\s*\)\s*(?=\()", changed_mask):
            if access.start() < poll.end():
                continue
            opening = access.end()
            depth, closing = 1, opening + 1
            while closing < len(changed_mask) and depth:
                depth += (changed_mask[closing] == "(") - (changed_mask[closing] == ")")
                closing += 1
            expression = changed_mask[opening:closing]
            if (not depth and opening <= match.start() and match.start() + len(literal) <= closing
                    and "|" in expression and ";" not in expression):
                accesses.append(access)
        if len(accesses) != 1:
            continue
        access = accesses[0]
        rewritten = changed[:access.start()] + "*(volatile u32 *)" + changed[access.end():]
        tag_changes.append({"symbol": symbol, "encoded_address": value, "kind": "volatile-or-address"})
    if len(tag_changes) != 1:
        return report
    # Both access edits follow the poll, so its original masked spans remain valid.
    local = "mmio_status"
    while re.search(r"\b" + local + r"\b", masked):
        local += "_"
    read = f"({local} = (*(volatile u32 *)0x{address:08X}U))"
    replacement = f"if ({read} & {poll['mask']}) {{\n        do {{\n\n        }} while ({read} & {poll['mask']});\n    }}"
    rewritten = rewritten[:poll.start()] + replacement + rewritten[poll.end():]
    rewritten = f"\n    register u32 {local};" + rewritten
    report["source"] = source[:definition.end()] + rewritten + source[end - 1:]
    report["changes"] = tag_changes + [{"kind": "encoded-register-poll", "symbol": poll["reg"],
        "address": address, "mask": mask, "load_witnesses": evidence, "mask_instruction_words": masks}]
    return report

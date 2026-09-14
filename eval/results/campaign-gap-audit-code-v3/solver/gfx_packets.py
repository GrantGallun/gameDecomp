"""Read-only candidates for constant Gfx packets written by MIPS functions.

Uses existing CFG/must-dataflow. Only pairs written through a freshly loaded,
explicitly named buffer-pointer global in one basic block are considered.
Dynamic words, cross-block pairs and multi-packet macros remain unresolved.
Decoding packets does not recover the CPU code that computes dynamic arguments.
"""
from __future__ import annotations

import hashlib
import re
from . import cfg, dataflow


def numeric_immediates(assembly: str) -> str:
    """Normalize splat's literal expressions on a copy, checked against words."""
    pattern = re.compile(
        r"(/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s*\*/\s*)"
        r"(lui|ori)(\s+.*?,\s*)\((0x[0-9A-Fa-f]+)\s*(>>|&)\s*(16|0xFFFF)\)")
    def replace(match):
        number, op, operand = int(match[5], 16), match[6], match[7]
        if (op, operand) not in {(">>", "16"), ("&", "0xFFFF")}:
            return match[0]
        immediate = number >> 16 if op == ">>" else number & 0xFFFF
        word = int(match[2], 16)
        if word >> 26 != {"lui": 15, "ori": 13}[match[3]] or word & 0xFFFF != immediate:
            raise ValueError("literal expression disagrees with encoded instruction")
        return match[1] + match[3] + match[4] + hex(immediate)
    return pattern.sub(replace, assembly)


def extract(assembly: str, *, pointer_symbol: str) -> dict:
    normalized = numeric_immediates(assembly)
    flow = dataflow.analyse(normalized)
    packets = []
    for block in flow.graph.blocks.values():
        pending = {}
        for insn in block.instructions:
            if insn.opcode in cfg.CALL_OPS or (insn.opcode in dataflow.STORE_WIDTH and insn.opcode != "sw"):
                pending.clear()
            state = flow.instruction_in.get(insn.index)
            if state is None:
                continue
            access = flow.accesses.get(insn.index)
            if insn.opcode == "sw" and access and access.address:
                address = access.address
                if (address.kind == "load" and address.width == 4 and address.inner ==
                        dataflow.Value.address(pointer_symbol) and address.offset in {0, 4}):
                    memory = dataflow.MEMORY.match(", ".join(insn.operands))
                    if memory:
                        base = dataflow.reg(memory["base"])
                        source = dataflow.reg(memory["value"])
                        value = dataflow.Value.constant(0) if source == "zero" else state.registers.get(source)
                        word = value.offset & 0xFFFFFFFF if value and value.kind == "constant" else None
                        words = pending.setdefault(base, {})
                        words[address.offset] = {"word": word, "instruction": insn.index, "text": insn.text}
                        if set(words) == {0, 4}:
                            packets.append({"block": block.id, "pointer_symbol": pointer_symbol,
                                "base_register": base, "stores": [words[0], words[4]],
                                "status": "constant" if all(w["word"] is not None for w in words.values()) else "dynamic-word"})
                            pending.pop(base)
            # Reusing a register must not merge different loaded pointer epochs.
            if insn.opcode in dataflow.WRITES_FIRST and insn.operands:
                pending.pop(dataflow.reg(insn.operands[0]), None)
    return {"assembly_sha256": hashlib.sha256(assembly.encode()).hexdigest(),
            "pointer_symbol": pointer_symbol, "packets": packets,
            "scope": "same-block candidate packets; no dynamic arguments inferred"}


def decode(words: list[int], *, microcode: str) -> dict:
    """Explicit optional libgfxd dependency; decoder outputs remain hypotheses."""
    import pygfxd as gfx
    if microcode not in {"f3d", "f3db", "f3dex", "f3dexb", "f3dex2"}:
        raise ValueError("unsupported microcode")
    if len(words) != 2 or any(not 0 <= word <= 0xFFFFFFFF for word in words):
        raise ValueError("one complete 64-bit packet is required")
    output = []
    def write(data, count):
        output.append(data[:count].decode("ascii"))
        return count
    gfx.gfxd_target(getattr(gfx, "gfxd_" + microcode))
    gfx.gfxd_endian(gfx.GfxdEndian.big, 4)
    gfx.gfxd_dynamic(None)
    gfx.gfxd_macro_fn(None)
    gfx.gfxd_input_buffer(b"".join(word.to_bytes(4, "big") for word in words))
    gfx.gfxd_output_callback(write)
    try:
        code = gfx.gfxd_execute()
        return {"microcode": microcode, "returncode": code, "macro": "".join(output)}
    finally:
        gfx.gfxd_output_callback(None)
        gfx.gfxd_input_buffer(None)

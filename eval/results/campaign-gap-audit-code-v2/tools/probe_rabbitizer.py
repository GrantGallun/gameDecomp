"""Probe rabbitizer's memory-access API so miner/evidence.py encodes the real
mapping rather than an assumed one. Run once; the output is the spec.
"""

import rabbitizer

# opcode field for each I-type memory instruction we care about
OPS = {
    "lb": 0x20, "lbu": 0x24, "lh": 0x21, "lhu": 0x25, "lw": 0x23,
    "lwl": 0x22, "lwr": 0x26, "lwu": 0x27,
    "sb": 0x28, "sh": 0x29, "sw": 0x2B, "swl": 0x2A, "swr": 0x2E,
    "lwc1": 0x31, "swc1": 0x39, "ldc1": 0x35, "sdc1": 0x3D,
    "ld": 0x37, "sd": 0x3F,
}

BASE_REG = 4   # $a0
DEST_REG = 2   # $v0


def encode(op, imm=0x24):
    return (op << 26) | (BASE_REG << 21) | (DEST_REG << 16) | (imm & 0xFFFF)


def main():
    hdr = "{:6} {:6} {:6} {:6} {:6} {:6} {:5} {}".format(
        "op", "load", "store", "deref", "unsign", "float", "dbl", "accessType")
    print(hdr)
    print("-" * len(hdr))

    for name, op in OPS.items():
        insn = rabbitizer.Instruction(encode(op), vram=0x80000000)
        try:
            access = insn.getAccessType()
        except Exception as exc:
            access = "ERR:{}".format(type(exc).__name__)
        print("{:6} {:6} {:6} {:6} {:6} {:6} {:5} {}".format(
            insn.getOpcodeName(),
            str(insn.doesLoad()),
            str(insn.doesStore()),
            str(insn.doesDereference()),
            str(insn.doesUnsignedMemoryAccess()),
            str(insn.isFloat()),
            str(insn.isDouble()),
            access,
        ))

    print()
    pos = rabbitizer.Instruction(encode(OPS["lw"], 0x24))
    neg = rabbitizer.Instruction(encode(OPS["lw"], 0xFFFC))
    print("positive immediate :", hex(pos.getProcessedImmediate()))
    print("negative immediate :", neg.getProcessedImmediate())
    print("base register (rs) :", str(pos.rs))
    print("dest register (rt) :", str(pos.rt))

    # AccessType enum members, so we can map them to widths
    print()
    if hasattr(rabbitizer, "AccessType"):
        members = [m for m in dir(rabbitizer.AccessType) if not m.startswith("_")]
        print("AccessType members :", ", ".join(members))


if __name__ == "__main__":
    main()

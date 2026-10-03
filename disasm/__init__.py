"""Own disassembly front end: ROM -> segments -> code/data -> functions.

Every stage here reads the ROM and nothing else. A reference decomp's yaml, ELF
or symbol_addrs may GRADE a stage (`disasm.grade`) but never feeds one -- the
same rule CLAUDE.md applies to ground truth for the miner. A new game has no
reference, so a stage that peeks at one is a stage that does not exist yet.
"""

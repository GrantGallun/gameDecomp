"""Binary observations and the fixed, source-independent identity policy."""

from pathlib import Path

import pytest

rabbitizer = pytest.importorskip("rabbitizer")

from miner import evidence
from solver import binary_type_facts, binary_type_identity


def function(name, addr, words):
    return evidence.Func(addr, name, [
        evidence.Insn(addr + 4 * i, word,
                      rabbitizer.Instruction(word, vram=addr + 4 * i))
        for i, word in enumerate(words)
    ])


def test_walker_records_memory_width_pointer_child_and_argument_reads():
    func = function("read_field", 0x1000, [
        0x8C880024,  # lw t0, 0x24(a0)
        0x91020003,  # lbu v0, 3(t0)
        0x03E00008,  # jr ra
        0,
    ])
    row = binary_type_facts.walk(func)
    assert row["function"] == "read_field" and row["addr"] == 0x1000
    assert [("P", "read_field", 0), 0x24, 4, 1, 1, "int"] in row["accesses"]
    assert [("D", ("P", "read_field", 0), 0x24), 3, 1, 0, 1, "int"] in row["accesses"]
    assert row["arity_reads"] == [0]


def test_cfg_meet_drops_pointer_only_defined_on_one_branch():
    func = function("branch", 0x1000, [
        0x10A00004,  # beq a1, zero, 0x1014
        0,
        0x00804021,  # addu t0, a0, zero
        0x10000002,  # b 0x1018
        0,
        0,
        0x8D020004,  # lw v0, 4(t0); t0 is unknown at this meet
        0x03E00008,
        0,
    ])
    assert binary_type_facts.walk(func)["accesses"] == []


def test_facts_uses_explicit_elf_and_returns_each_function(monkeypatch):
    seen = []
    elf = Path("target.elf")
    funcs = [function("empty", 0x1000, [0x03E00008, 0])]

    def disassemble(path):
        seen.append(path)
        return funcs

    monkeypatch.setattr(evidence, "disassemble", disassemble)
    rows = binary_type_facts.facts(elf)
    assert seen == [elf]
    assert len(rows) == 1 and rows[0]["function"] == "empty"


def test_d32_propagates_identity_across_dereferenced_call_argument():
    caller = function("caller", 0x1000, [0x0C000800, 0, 0x03E00008, 0])
    callee = function("callee", 0x2000, [0x8C820020, 0x03E00008, 0])
    rows = [binary_type_facts.walk(caller), binary_type_facts.walk(callee)]
    assert [0x2000, 0, ('P', 'caller', 0)] in rows[0]["calls"]
    uf = binary_type_identity.solve(rows)
    assert uf.find(('P', 'caller', 0)) == uf.find(('P', 'callee', 0))


def test_d32_does_not_invent_identity_without_threshold_evidence():
    rows = [
        {"function": "caller", "addr": 0x1000, "arity_reads": [0],
         "accesses": [], "unify": [], "returns": [],
         "calls": [[0x2000, 0, ['P', 'caller', 0]]]},
        {"function": "callee", "addr": 0x2000, "arity_reads": [0],
         "accesses": [[['P', 'callee', 0], 0x1C, 4, 1, 1, 'int']],
         "unify": [], "returns": [], "calls": []},
    ]
    uf = binary_type_identity.solve(rows)
    assert uf.find(('P', 'caller', 0)) != uf.find(('P', 'callee', 0))

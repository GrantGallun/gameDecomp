"""Exactness packets report byte facts without promoting them to verdicts."""

from __future__ import annotations

import struct

from solver import residual, workspace


def _elf32_text(payload: bytes) -> bytes:
    """Small big-endian ELF32 fixture with .shstrtab and .text."""
    names = b"\0.shstrtab\0.text\0"
    header_size = 52
    section_size = 40
    section_count = 3
    section_offset = header_size
    names_offset = section_offset + section_size * section_count
    text_offset = names_offset + len(names)
    data = bytearray(text_offset + len(payload))
    data[:16] = b"\x7fELF\x01\x02\x01" + b"\0" * 9
    struct.pack_into(">HHIIIIIHHHHHH", data, 16,
                     1, 8, 1, 0, 0, section_offset, 0,
                     header_size, 0, 0, section_size, section_count, 1)
    struct.pack_into(">IIIIIIIIII", data, section_offset + section_size,
                     1, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0)
    struct.pack_into(">IIIIIIIIII", data, section_offset + 2 * section_size,
                     11, 1, 6, 0, text_offset, len(payload), 0, 0, 4, 0)
    data[names_offset:names_offset + len(names)] = names
    data[text_offset:] = payload
    return bytes(data)


def test_elf_text_reads_big_endian_mips_style_object(tmp_path):
    obj = tmp_path / "candidate.o"
    obj.write_bytes(_elf32_text(b"\x01\x02\x03\x04"))

    assert residual.elf_text(obj) == b"\x01\x02\x03\x04"
    assert residual.elf_text(tmp_path / "missing.o") is None


def test_packet_separates_weighted_score_from_raw_byte_distance(tmp_path):
    target = tmp_path / "target.o"
    candidate = tmp_path / "candidate.o"
    target.write_bytes(_elf32_text(bytes([1, 2, 3, 4, 5, 6, 7, 8])))
    candidate.write_bytes(_elf32_text(bytes([1, 9, 3, 4])))
    attempt = workspace.Attempt(
        True, 95.346, False,
        "--- target\n+++ candidate\n-lw v0,0(a0)\n+lw v0,4(a0)\n-nop\n",
        "", "")

    packet = residual.build(
        attempt, target_asm="glabel f\nlw v0,0(a0)\nnop\n",
        target_object=target, candidate_object=candidate)

    assert packet.weighted_progress_score == 95.346
    assert packet.exact is False
    assert packet.target_text_bytes == 8
    assert packet.candidate_text_bytes == 4
    assert packet.text_length_delta == -4
    assert packet.positional_byte_distance == 5
    assert packet.positional_equal_bytes == 3
    assert packet.target_instructions == 2
    assert packet.candidate_instructions == 1
    assert packet.instruction_delta == 1
    assert "weighted_progress_score" in packet.render()


def test_noncompiling_packet_reports_error_without_fake_byte_metrics():
    attempt = workspace.Attempt(False, 0.0, False, "", "x undefined", "")

    packet = residual.build(attempt, target_asm="glabel f\njr ra\nnop")

    assert not packet.compiled
    assert packet.compiler_error_signature
    assert packet.candidate_instructions is None
    assert packet.positional_byte_distance is None

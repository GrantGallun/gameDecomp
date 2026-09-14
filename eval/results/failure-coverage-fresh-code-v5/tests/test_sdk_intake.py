import pytest

from solver import sdk_intake as sdk


def test_segment_mapping_and_boundaries():
    config = {"segments": [{"type": "code", "start": 0x1000, "vram": 0x80000400}, [0x2000]]}
    assert sdk.rom_offset(config, 0x80000410, 16, 0x3000) == 0x1010
    for address, size in [(0x800003fc, 16), (0x800013fc, 16), (0x80000400, 3)]:
        with pytest.raises(ValueError):
            sdk.rom_offset(config, address, size, 0x3000)


def test_disassembly_coverage_classification_and_rendering():
    rows = sdk.instructions("""
80000400: 8c820000 lw v0,0(a0)
80000404: 8c590000 lw t9,0(v0)
80000408: 03e00008 jr ra
8000040c: ac990000 sw t9,0(a0)
""", 0x80000400, 16)
    assert sdk.classify(rows)["status"] == "ordinary_straight_line"
    assert "sw $t9,0($a0)" in sdk.render("pop", rows)
    rows[0]["opcode"] = "mfc0"
    assert sdk.classify(rows)["status"] == "hardware_backend_required"
    rows[0]["opcode"] = "jal"
    assert sdk.classify(rows)["status"] == "sdk_control_flow_or_relocation_unsupported"
    with pytest.raises(ValueError):
        sdk.instructions("80000400: 03e00008 jr ra", 0x80000400, 16)


def test_unsupported_dialect_reports_specific_evidence_with_bounded_examples():
    rows = [dict(pc=0x80000000+4*i,word='00000000',opcode='bne',operands='a0,a1,80000000')
            for i in range(20)]
    rows += [dict(pc=0x80000050,word='03e00008',opcode='jr',operands='ra'),
             dict(pc=0x80000054,word='00000000',opcode='nop',operands='')]
    report = sdk.classify(rows)
    assert report['terminal_return_shape_valid'] is True
    assert report['unsupported_opcode_counts'] == {'bne':20}
    assert len(report['unsupported_instructions']) == 16
    assert report['omitted_unsupported_instructions'] == 4
    assert report['instruction_count'] == 22
    rows[-2]['opcode'] = 'nop'
    assert sdk.classify(rows)['terminal_return_shape_valid'] is False
    rows[-2]['opcode'] = 'bne'
    assert sdk.classify(rows)['unsupported_opcode_counts'] == {'bne':21}

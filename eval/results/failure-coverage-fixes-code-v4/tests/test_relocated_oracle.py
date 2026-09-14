"""The relocation fallback must require resolved text and checked data."""

import pytest

from tools import relocated_oracle


def test_assignments_are_bounded_and_reject_shell_syntax():
    assert relocated_oracle.parse_assignment(".rodata=0x800E1A80") == (
        ".rodata", 0x800E1A80)
    with pytest.raises(ValueError, match="invalid section"):
        relocated_oracle.parse_assignment(".rodata;rm=0")
    with pytest.raises(ValueError, match="32-bit"):
        relocated_oracle.parse_assignment(".rodata=0x100000000")


def test_address_symbols_are_inferred_only_from_encoded_names():
    output = """         U D_800E1A8C
         U func_80001234
         U ordinary_call
00000000 T Defined
"""
    assert relocated_oracle.address_symbols(output) == {
        "D_800E1A8C": 0x800E1A8C,
        "func_80001234": 0x80001234,
    }


def test_undefined_symbols_are_parsed_for_shared_synthetic_linking():
    output = "         U semanticGlobal\n         U semanticCall\n00000000 T local\n"
    assert relocated_oracle.undefined_symbols(output) == {
        "semanticGlobal", "semanticCall"}


def test_linker_script_places_sections_and_defines_symbols():
    script = relocated_oracle.linker_script(
        0x80001000, {".late_rodata": 0x800E0000},
        {"D_800E0004": 0x800E0004})
    assert ". = 0x80001000;" in script
    assert ".text 0x80001000 : SUBALIGN(1) { *(.text) }" in script
    assert (".late_rodata 0x800E0000 : SUBALIGN(1) "
            "{ *(.late_rodata) }") in script
    assert "D_800E0004 = 0x800E0004;" in script


def test_exact_receipt_rejects_nonzero_or_unequal_data_rows():
    payload = {
        "schema_version": 1,
        "kind": "mips_relocated_function_oracle_receipt",
        "compiled": True,
        "oracle_tested": True,
        "exact": True,
        "status": "relocated_text_and_data_exact",
        "relocated_text": {"equal": True},
        "data_sections": [{
            "equal": True,
            "trailing_padding_bytes": 8,
            "trailing_padding_zero": False,
        }],
    }
    with pytest.raises(ValueError, match="nonzero trailing padding"):
        relocated_oracle.validate_exact_receipt(payload)

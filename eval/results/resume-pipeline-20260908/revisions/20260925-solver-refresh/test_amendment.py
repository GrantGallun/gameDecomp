"""Amendment tests, run against the FROZEN project with the refreshed solver installed there."""
import os
import struct
import sys
from pathlib import Path

PROJECT = Path(os.environ["AMENDMENT_PROJECT"])
sys.path.insert(0, str(PROJECT))

from solver import branch_shape, byte_certificate, regalloc_mutations, rodata_symbol  # noqa: E402

assert Path(regalloc_mutations.__file__).resolve() == (PROJECT / "solver/regalloc_mutations.py").resolve()


def test_stream_accepts_evidence_and_offers_the_new_families():
    source = "void f(void) {\n    int x;\n    x = 3;\n    if (g) {\n        x = 7;\n    }\n    h(x);\n}\n"
    diff = "--- t\n+++ c\n@@ -1,1 +1,1 @@\n-b    3e4\n li    s3,0x60\n"
    kinds = {k for _l, k, _c in regalloc_mutations.variants(source, "f", diff, evidence={})}
    assert "select_else" in kinds                 # branch_shape reached through the stream


def test_o1_register_saved_is_a_family():
    assert "o1_register_saved" in {k for k, _g in branch_shape.families("void f(void) {}", "f", "", None)}


def test_certificate_second_stages_exist_and_stay_narrow():
    a = ("external", "s", 1, 0)
    text = b"\x3c\x0e\x00\x00" * 4
    target = [(4, 5, a), (8, 6, a), (0, 5, a), (12, 6, a)]
    ido = [(0, 5, a), (8, 6, a), (4, 5, a), (12, 6, a)]
    assert byte_certificate.same_addend_pairing_groups(target, text) == byte_certificate.same_addend_pairing_groups(ido, text)
    left = {".text": {"sha256": "x", "size": 16, "relocations": target}}
    right = {".text": {"sha256": "y", "size": 16, "relocations": ido}}
    assert not byte_certificate.pairing_equivalent(left, right, {".text": text}, {".text": text})


def test_rodata_owner_imports_and_formats_exactly():
    text = rodata_symbol.literal_text("f32", struct.pack(">f", 4 / 3))
    assert struct.pack(">f", float(text.rstrip("f"))) == struct.pack(">f", 4 / 3)

"""Schema 3 admits jump tables: each entry linked to the function and compared with the ROM (solver.function_boundary).

Needs the IDO toolchain and the ROM (WSL); skipped elsewhere. Fixture: our own candidate for
updateRaceSplitscreenSelectOption1Frame (2026-09-30), instruction-identical except the jump-table relocation.
It was refused before ("relocated data (jump tables, pointer tables) is outside schema 3").
"""
import shutil
import struct
from pathlib import Path

import pytest

REPO = Path("/home/grant/decomp/sbk1")
FN = "updateRaceSplitscreenSelectOption1Frame"
FIXTURE = Path(__file__).parent / "fixtures" / f"jtbl_{FN}.c"
pytestmark = pytest.mark.skipif(not (REPO / "snowboardkids.z64").exists(), reason="needs the ROM and toolchain (WSL)")


@pytest.fixture(scope="module")
def certified():
    from eval import probe_source
    a = probe_source.probe(FN, FIXTURE.read_text(), "jtbl-fixture")
    return a.verification["function_boundary"]


def _args(receipt, candidate=None):
    paths = {k: Path(v["path"]) for k, v in receipt["inputs"].items()}
    if candidate is not None:
        paths["candidate"] = candidate
    return dict(**paths, function=receipt["function"], address=receipt["address"], size=receipt["size"])


def test_jump_table_function_is_certified_against_the_rom(certified):
    assert certified["function_exact"] is True and certified["schema_version"] == 3
    assert certified["status"] == "function_exact_pending_integration"
    assert [s["length"] for s in certified["data_sites"]] == [24]              # six entries


def test_a_wrong_jump_table_entry_is_refused(certified, tmp_path):
    from solver import function_boundary as fb
    original = Path(certified["inputs"]["candidate"]["path"])
    data = bytearray(original.read_bytes())
    rows, labels = fb._elf_sections(bytes(data))
    rodata = rows[labels.index(".rodata")]
    first = struct.unpack_from(">I", data, rodata[4])[0]
    struct.pack_into(">I", data, rodata[4], first + 4)                          # still inside the function
    bad = tmp_path / "bad.o"
    bad.write_bytes(bytes(data))
    out = fb.certify(**_args(certified, bad))
    assert out["function_exact"] is False and "differs from ROM" in out.get("schema_3_error", "")


def test_an_entry_outside_the_function_is_refused(certified, tmp_path):
    from solver import function_boundary as fb
    original = Path(certified["inputs"]["candidate"]["path"])
    data = bytearray(original.read_bytes())
    rows, labels = fb._elf_sections(bytes(data))
    rodata = rows[labels.index(".rodata")]
    struct.pack_into(">I", data, rodata[4], 0x10000)
    bad = tmp_path / "far.o"
    bad.write_bytes(bytes(data))
    out = fb.certify(**_args(certified, bad))
    assert out["function_exact"] is False and "outside the certified function" in out.get("schema_3_error", "")

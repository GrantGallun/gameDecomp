"""The two compiler-confirmed polling repairs must become executable proposals."""
import importlib
from pathlib import Path
import pytest

INPUTS = Path(__file__).resolve().parents[1] / "eval/results/repair-mechanisms-20260922/inputs"


def propose(source, function, assembly, **kwargs):
    try:
        module = importlib.import_module("solver.mmio_repair")
    except ModuleNotFoundError:
        return {"source": source, "changes": []}
    return module.propose(source, function, assembly, **kwargs)


@pytest.mark.parametrize("name", ["osEPiRawReadIo", "osEPiRawWriteIo"])
def test_motivating_poll_and_uncached_address_are_reconstructed(name):
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    report = propose(source, name, asm, big_endian_o32=True)
    assert report["changes"]
    assert "register u32 mmio_status;" in report["source"]
    assert "0xA4600010U" in report["source"]
    assert "0xA0000000U" in report["source"]
    assert "*(volatile u32 *)((u32)arg0->unkC" in report["source"]
    assert "(s32) &D_A0000000" not in report["source"]
    assert not propose(report["source"], name, asm, big_endian_o32=True)["changes"]


@pytest.mark.parametrize("change", ["wrong-abi", "unencoded", "changed-poll-mask", "clobbered-base", "nonempty-loop", "shadow", "escape"])
def test_incomplete_evidence_or_unsupported_source_declines(change):
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    if change == "unencoded":
        import re
        asm = re.sub(r"/\*[^*]+\*/", "", asm)
    if change == "changed-poll-mask":
        asm = asm.replace("30EF0003", "30EF0001")
    if change == "clobbered-base":
        asm = asm.replace("27BD0008 */  addiu      $sp, $sp, 0x8", "24010000 */  addiu      $at, $zero, 0x0")
    if change == "nonempty-loop":
        source = source.replace("do {", "do {\n            call();")
    if change == "shadow":
        source = source.replace("    if (", "    s32 PI_STATUS_REG;\n    if (")
    if change == "escape":
        source = source.replace("    return 0;", "    escape(&PI_STATUS_REG);\n    return 0;")
    assert not propose(source, name, asm, big_endian_o32=change != "wrong-abi")["changes"]


def test_names_do_not_select_the_operation_and_existing_local_names_do_not_collide():
    name = "osEPiRawReadIo"
    source = (INPUTS / name / "initial.c").read_text().replace(name, "arbitrary")
    source = source.replace("    if (", "    u32 mmio_status;\n    if (")
    asm = (INPUTS / name / "target.s").read_text().replace(name, "arbitrary")
    report = propose(source, "arbitrary", asm, big_endian_o32=True)
    assert report["changes"] and "register u32 mmio_status_;" in report["source"]


@pytest.mark.parametrize("old,new", [
    ("27BD0008 */  addiu      $sp, $sp, 0x8", "0C000100 */  jal        external"),
    ("27BDFFF8 */  addiu      $sp, $sp, -0x8", "0C000100 */  jal        external"),
    ("27BD0008", "24010000"),  # Encoded $at clobber contradicts printed $sp.
    ("27BDFFF8", "24070000"),  # Encoded polling value clobber.
    ("3C01A000", "3C21A000"),  # LUI's reserved rs bits.
    ("01215025", "01215065"),  # OR's reserved shift bits.
    ("3C0EA460", "3C2EA460"),  # Poll address LUI's reserved rs bits.
    ("    /* B0A34", "  alternate_entry:\n    /* B0A34"),
    ("27BD0008 */  addiu", "27BD0008 */  alternate_entry: addiu"),
    ("27BDFFF8 */  addiu", "27BDFFF8 */  alternate_entry: addiu"),
])
def test_interrupted_or_contradictory_encoded_chains_decline(old, new):
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    assert old in asm
    assert not propose(source, name, asm.replace(old, new), big_endian_o32=True)["changes"]


@pytest.mark.parametrize("discard", ["seed", "address"])
def test_discarded_zero_register_values_are_not_address_evidence(discard):
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    if discard == "seed":
        asm = asm.replace("3C01A000 */  lui        $at", "3C00A000 */  lui        $zero")
        asm = asm.replace("01215025 */  or         $t2, $t1, $at", "01205025 */  or         $t2, $t1, $zero")
    else:
        asm = asm.replace("01215025 */  or         $t2, $t1, $at", "01210025 */  or         $zero, $t1, $at")
        asm = asm.replace("AD460000 */  sw         $a2, %lo(D_A0000000)($t2)",
                          "AC060000 */  sw         $a2, %lo(D_A0000000)($zero)")
    assert not propose(source, name, asm, big_endian_o32=True)["changes"]


def test_comments_and_strings_are_preserved_and_cannot_select_accesses():
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    literal = '    const char *note = "*(s32 *)((s32) &D_A0000000 | address)";\n'
    comment = "    /* (s32) &D_A0000000 */\n"
    start = source.index("    if (")
    end = source.index("    *(s32 *)", start)
    fake_poll = "    /* " + source[start:end] + " */\n"
    source = source[:start] + literal + comment + fake_poll + source[start:]
    report = propose(source, name, asm, big_endian_o32=True)
    assert report["changes"]
    assert literal in report["source"] and comment in report["source"] and fake_poll in report["source"]
    assert "*(volatile u32 *)((u32)arg0->unkC" in report["source"]


def test_address_cast_outside_the_word_access_is_not_repaired():
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    source = source.replace("(s32) &D_A0000000", "0xA0000000U")
    source = source.replace("    *(s32 *)", "    arg1 = (s32) &D_A0000000;\n    *(s32 *)")
    assert not propose(source, name, asm, big_endian_o32=True)["changes"]


@pytest.mark.parametrize("symbol", ["PI_STATUS_REG", "D_A0000000"])
def test_parameter_shadow_is_not_treated_as_a_hardware_symbol(symbol):
    name = "osEPiRawWriteIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    source = source.replace("s32 arg2) {", "s32 arg2, s32 " + symbol + ") {")
    assert not propose(source, name, asm, big_endian_o32=True)["changes"]


def test_registered_action_requires_workspace_target_and_never_claims_exact(tmp_path):
    from eval import tool_registry
    assert "repair-mmio" in tool_registry.ACTIONS
    name = "osEPiRawReadIo"
    source = (INPUTS / name / "initial.c").read_text()
    asm = (INPUTS / name / "target.s").read_text()
    (tmp_path / "target.s").write_text(asm)
    header = bytearray(52)
    header[:6] = b"\x7fELF\x01\x02"
    header[18:20] = (8).to_bytes(2, "big")
    header[36:40] = (0x1000).to_bytes(4, "big")
    (tmp_path / "target.o").write_bytes(header)
    context = {"function": name, "candidate": source, "workspace": str(tmp_path),
               "target_asm_path": str(tmp_path / "target.s")}
    runner = tool_registry.ACTIONS["repair-mmio"].resolve()
    result = runner(context, {})
    assert result["changed"] and not result["exact"]
    assert result["detail"]["changes"]
    context["target_asm_path"] = str(tmp_path / "another.s")
    result = runner(context, {})
    assert not result["changed"] and result["reason"]

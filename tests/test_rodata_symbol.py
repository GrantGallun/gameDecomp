"""solver.rodata_symbol fires on its motivating residual and declines without byte-equal ROM evidence."""
import struct

from solver import rodata_symbol
from solver.source_attribution import sha

SOURCE = """void initControllerPakFileDeleteFlow(void) {
    resetAllViewports();
    configureViewport(0, 0xA0, 0x78, 0x120U, 0xD0U, 0x140U, 0xF0U, -1.33333333f);
}
"""
DIFF = """--- target
+++ candidate
@@ -1,2 +1,2 @@
-lui    at,%hi(D_800E0A50)
-lwc1    $f4,%lo(D_800E0A50)(at)
+lui    at,%hi(.rodata)
+lwc1    $f4,%lo(.rodata)(at)
"""


def _attribution(source):
    return {"status": "verified", "source_sha256": sha(source),
            "instructions": [{"normalized_line": 1, "candidate_line": 3},
                             {"normalized_line": 2, "candidate_line": 3}]}


def test_rodata_symbol_fires_on_the_viewport_literal(monkeypatch):
    rom = struct.pack(">f", 1.33333333)            # the ROM holds +4/3; the draft's literal was sign-flipped
    monkeypatch.setattr(rodata_symbol, "rom_bytes", lambda elf, vaddr, size: rom if vaddr == 0x800E0A50 else None)
    out = dict(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", DIFF, _attribution(SOURCE), elf="x"))
    assert list(out) == ["rodata_literal:1.3333334f@3", "rodata_symbol:D_800E0A50@3"]
    assert "0xF0U, 1.3333334f);" in out["rodata_literal:1.3333334f@3"]      # sign fixed from the ROM value
    cand = out["rodata_symbol:D_800E0A50@3"]
    assert "0xF0U, D_800E0A50);" in cand and "-1.33333333f" not in cand
    assert cand.startswith("extern f32 D_800E0A50;\nvoid initControllerPakFileDeleteFlow")


def test_rodata_symbol_declines_on_value_mismatch_missing_attribution_or_addend(monkeypatch):
    # A literal whose value disagrees with the ROM: the SYMBOL form declines (it cannot tell which literal feeds the
    # load), while the LITERAL form writes the ROM's value into the one float literal on the attributed line -- the
    # ROM is the truth, and a wrong literal is exactly the fault (initControllerPakFileDeleteFlow's sign flip).
    monkeypatch.setattr(rodata_symbol, "rom_bytes", lambda elf, vaddr, size: struct.pack(">f", 2.0))
    out = dict(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", DIFF, _attribution(SOURCE), elf="x"))
    assert list(out) == ["rodata_literal:2.0f@3"]
    monkeypatch.setattr(rodata_symbol, "rom_bytes", lambda elf, vaddr, size: struct.pack(">f", 1.33333333))
    assert not list(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", DIFF, None, elf="x"))
    stale = dict(_attribution(SOURCE), source_sha256="other")
    assert not list(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", DIFF, stale, elf="x"))
    addend = DIFF.replace("%lo(D_800E0A50)(at)", "%lo(D_800E0A50+0x4)(at)")
    assert not list(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", addend, _attribution(SOURCE), elf="x"))


def test_symbol_address_reads_the_name_or_the_map():
    assert rodata_symbol.symbol_address("D_800E0A50") == 0x800E0A50
    assert rodata_symbol.symbol_address("gTable", "    0x00000000800f1234                gTable\n") == 0x800F1234
    assert rodata_symbol.symbol_address("gMissing", "") is None


def test_unary_minus_goes_with_the_literal_and_binary_minus_stays():
    rom = struct.pack(">f", 1.5)
    assert rodata_symbol._replace_on_line("x = f(a, -1.5f);", 1, "f32", rom, "D_1") == "x = f(a, D_1);"
    assert rodata_symbol._replace_on_line("x = a - 1.5f;", 1, "f32", rom, "D_1") == "x = a - D_1;"
    assert rodata_symbol._replace_on_line("x = a * 2.5f;", 1, "f32", rom, "D_1") is None


def test_section_relative_target_gets_the_literal_from_the_target_object(monkeypatch, tmp_path):
    late = struct.pack(">f", 4 / 3) + bytes(12)
    monkeypatch.setattr(rodata_symbol, "_section_bytes", lambda obj, section: late if section == ".late_rodata" else None)
    diff = DIFF.replace("%hi(D_800E0A50)", "%hi(.late_rodata)").replace("%lo(D_800E0A50)", "%lo(.late_rodata)")
    out = dict(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", diff, _attribution(SOURCE), elf="x",
                                      target_obj=tmp_path / "target.o"))
    assert list(out) == ["rodata_literal:1.3333334f@3"]
    assert "0xF0U, 1.3333334f);" in out["rodata_literal:1.3333334f@3"]
    # without the target object there is no value evidence: decline
    assert not list(rodata_symbol.variants(SOURCE, "initControllerPakFileDeleteFlow", diff, _attribution(SOURCE), elf="x"))


def test_literal_text_round_trips_exactly():
    for v in (4 / 3, 0.1, -2.5, 1e-7, 1024.0):
        text = rodata_symbol.literal_text("f32", struct.pack(">f", v))
        assert struct.pack(">f", float(text.rstrip("f"))) == struct.pack(">f", v)


# --- address-taken rodata (rodata_symbol.address_rewrite) ------------------------------------------------------------
# Replays the facts read from real objects (eval/results/hidden-object-20260930/address_probe.py): six functions whose
# .text is byte-identical to the target and which the function certificate refused over their rodata. Each rewrite
# was function_exact under schema 3 when scored.
import json as _json
from pathlib import Path as _Path

CASES = {c["function"]: c for c in _json.loads(
    (_Path(__file__).parent / "fixtures" / "rodata_address_cases.json").read_text(encoding="utf-8"))}


def test_address_rewrite_fires_on_every_motivating_residual():
    fired = {}
    for name, case in CASES.items():
        out, receipt = rodata_symbol.address_rewrite(case["source"], name, case["facts"])
        assert out == case["rewritten"], name                      # deterministic replay of the scored rewrite
        fired[name] = out is not None
        if out is not None:
            assert case["after"]["function_exact"] is True and case["before"]["function_exact"] is False, name
    assert sorted(n for n, f in fired.items() if f) == sorted(
        ["func_8005A884", "func_8005CF60", "updateEndingObjectSpriteDebugViewer", "func_8005C14C",
         "func_8005D558", "func_8005AC44"])
    # already named correctly: nothing to do, and it says so
    assert not fired["func_8005905C"]


def test_each_source_shape_becomes_the_target_label():
    out, r = rodata_symbol.address_rewrite(CASES["func_8005A884"]["source"], "func_8005A884", CASES["func_8005A884"]["facts"])
    assert r["definitions"] == {"gRaceUiCourseValueFormat": "D_800E12F4"}
    assert "extern const char D_800E12F4[];" in out and "gRaceUiCourseValueFormat" not in out
    out, r = rodata_symbol.address_rewrite(CASES["func_8005D558"]["source"], "func_8005D558", CASES["func_8005D558"]["facts"])
    assert r["renamed"] == {"gRaceUiTrickValueFormat": "D_800E1454"} and "gRaceUiTrickValueFormat" not in out
    name = "updateEndingObjectSpriteDebugViewer"
    out, r = rodata_symbol.address_rewrite(CASES[name]["source"], name, CASES[name]["facts"])
    assert 'rmonPrintf(D_800E1070,' in out and "extern const char D_800E1070[];" in out
    out, r = rodata_symbol.address_rewrite(CASES["func_8005C14C"]["source"], "func_8005C14C", CASES["func_8005C14C"]["facts"])
    assert len(r["dropped_unread"]) == 10 and '"-Target-"' not in out


def test_address_rewrite_declines_without_evidence_or_when_layout_does_not_reproduce():
    name = "updateEndingObjectSpriteDebugViewer"
    source, facts = CASES[name]["source"], CASES[name]["facts"]
    assert rodata_symbol.address_rewrite(source, name, {"sites": []})[0] is None
    # a candidate .rodata the source's literals do not rebuild: the pairing is not guessed
    bad = dict(facts, candidate_rodata="00" + facts["candidate_rodata"][2:])
    out, r = rodata_symbol.address_rewrite(source, name, bad)
    assert out is None and "do not reproduce" in r["declined"][0]
    # a datum offset no literal starts at
    shifted = dict(facts, sites=[dict(facts["sites"][0], candidate=["section", 4])])
    out, r = rodata_symbol.address_rewrite(source, name, shifted)
    assert out is None and "no literal starts" in r["declined"][0]


def test_c_string_decodes_escapes():
    assert rodata_symbol.c_string(r"x = %d  y = %d \n") == b"x = %d  y = %d \n"
    assert rodata_symbol.c_string(r'%2.2d\'%2.2d\"') == b"%2.2d'%2.2d\""
    assert rodata_symbol.c_string(r"\101\x42") == b"AB"
    assert rodata_symbol.c_string(r"\q") is None


def test_symbol_site_renames_to_the_target_symbol_and_keeps_its_declared_type():
    # drawCharacterSelectCourseExitPreviewPanel (2026-09-30): a name the ROM does not define, at the site where the
    # byte-identical target names gCharacterSelectCourseExitPreviewData; renamed -> object exact.
    source = "extern u16 gCorner;\nvoid f(void) {\n    g(&gCorner);\n}\n"
    facts = {"sites": [{"at": 8, "target_label": "gData", "candidate": ["external", "gCorner"], "symbol": True}],
             "candidate_symbols": {}, "candidate_rodata": ""}
    out, receipt = rodata_symbol.address_rewrite(source, "f", facts)
    assert receipt["renamed"] == {"gCorner": "gData"}
    assert out == "extern u16 gData;\nvoid f(void) {\n    g(&gData);\n}\n"      # no `extern const char gData[];`

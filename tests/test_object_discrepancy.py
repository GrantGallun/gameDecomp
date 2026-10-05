"""object_discrepancy must FIRE on its motivating residual: the 24 functions whose normalized asm
reads exact while the byte certificate rejects the object (kb-sbk1, measured 2026-09-30)."""
import json
from collections import defaultdict
from pathlib import Path

from solver import object_discrepancy as od

CASES = json.loads((Path(__file__).parent / "fixtures" / "hidden_from_asm.json")
                   .read_text(encoding="utf-8"))["cases"]


def rows():
    return {c["function"]: od.compare(c["target_image"], c["candidate_image"]) for c in CASES}


def test_fixture_is_the_24_hidden_functions():
    assert len(CASES) == 24
    assert all(od.hidden_from_asm(c) for c in CASES)


def test_fires_on_every_hidden_function():
    # The silent decline: a pass that returns nothing on the residual it owns.
    silent = [name for name, found in rows().items() if not found]
    assert silent == []


def test_rows_are_well_formed_and_no_lever_is_assumed():
    for name, found in rows().items():
        for row in found:
            assert row.kind in od.KINDS, (name, row.kind)
            assert row.lever == od.UNKNOWN, (name, row)


def test_trailing_size_gap_is_found_in_ten_functions_all_sixteen_bytes():
    sized = {n: [r for r in found if r.kind == "section_size"] for n, found in rows().items()}
    sized = {n: r for n, r in sized.items() if r}
    assert len(sized) == 10
    assert all(len(r) == 1 and r[0].section == ".text" and r[0].detail == "target longer by 16"
               for r in sized.values())


def test_section_against_symbol_relocation_is_identified():
    found = defaultdict(list)
    for name, got in rows().items():
        found[name] = [r for r in got if r.kind == "reloc_identity" and r.detail == "section_vs_external"]
    assert sorted(n for n, r in found.items() if r) == ["func_8005905C", "func_8005C14C"]
    row = found["func_8005905C"][0]
    assert row.target[2][0] == "section" and row.target[2][1] == ".rodata"
    assert row.candidate[2] == ("external", "D_800E128C", 1, 0)


def test_order_only_relocation_differences_are_reported_as_order():
    order = [n for n, got in rows().items() if any(r.kind == "reloc_order" for r in got)]
    assert len(order) >= 11
    assert all(not any(r.kind in {"reloc_identity", "reloc_missing", "reloc_extra"} for r in got)
               for n, got in rows().items() if n in order)


def test_identical_images_have_no_discrepancies():
    image = CASES[0]["target_image"]
    assert od.compare(image, image) == []


def test_c_line_attached_only_for_verified_attribution_at_matching_offset():
    row = od.Discrepancy("reloc_identity", ".text", 240, None, None)
    attribution = {"status": "verified", "instructions": [
        {"section": ".text", "address": 240, "candidate_line": 12, "normalized_line": 61, "instruction": "lui a0,0x0"}]}
    assert od.attach_c_lines([row], attribution)[0].candidate_line == 12
    assert od.attach_c_lines([row], {**attribution, "status": "unavailable"})[0].candidate_line is None


# --- routing (classify / same_text) ------------------------------------------------------------------------------------

def _text_identical(case):
    # The fixture holds images, not bytes: sha equality, or the measured 16-byte trailing-padding group.
    t, c = case["target_image"]["sections"][".text"], case["candidate_image"]["sections"][".text"]
    return t["sha256"] == c["sha256"] or t["size"] - c["size"] == 16


def test_invariant_normalized_exact_implies_certificate_or_explained_row():
    # Explained-or-broken for the normalizer: every candidate the normalized asm calls exact while the certificate
    # does not must route to `certify` or a lever -- never `unexplained`, never `c_edit`. A new lossy normalization
    # that hides something else lands here as a failure instead of in a census months later.
    routes = {}
    for case in CASES:
        rows = od.compare(case["target_image"], case["candidate_image"])
        routes[case["function"]] = od.classify(rows, text_identical=_text_identical(case))["route"]
    assert set(routes.values()) <= {"certify", "generator"}, {n: r for n, r in routes.items()
                                                              if r not in {"certify", "generator"}}


def test_classify_routes_each_kind():
    rodata_extra = od.Discrepancy("section_extra", ".rodata", None, None, 32)
    bss_extra = od.Discrepancy("section_extra", ".bss", None, None, 64)
    text_bytes = od.Discrepancy("section_bytes", ".text", None, "a", "b")
    assert od.classify([], text_identical=True)["route"] == "exact"
    assert od.classify([rodata_extra], text_identical=True)["route"] == "certify"
    assert od.classify([rodata_extra], text_identical=True, address_sites=1)["levers"] == ["rodata_address"]
    mixed = od.classify([text_bytes, rodata_extra], text_identical=False)
    assert mixed["route"] == "c_edit" and mixed["hidden"] == 1 and mixed["unexplained"] == []
    # an extra .bss with .text identical and no unused definition behind it is a finding, not a null
    bare = od.classify([bss_extra], text_identical=True)
    assert bare["route"] == "unexplained" and bare["unexplained"] == ["section_extra:.bss"]
    lever = od.classify([bss_extra], text_identical=True, unused_objects=1)
    assert lever["route"] == "generator" and lever["levers"] == ["unused_file_scope_object"]


def test_same_text_admits_only_trailing_zero_padding():
    code = bytes.fromhex("27bdffe8afbf0014")
    assert od.same_text(code, code)
    assert od.same_text(code + bytes(16), code)
    assert not od.same_text(code + b"\0\0\0\1", code)
    assert not od.same_text(code, code[:4] + b"\0\0\0\0")
    assert not od.same_text(None, code)

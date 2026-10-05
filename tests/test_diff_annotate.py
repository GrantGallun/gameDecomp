from solver import diff_annotate

DIFF = """--- t
+++ c
@@ -10,5 +10,5 @@
 addiu    sp,sp,-24
-lb    v1,0x10(s0)
+lb    v0,0x10(s0)
 nop
-lb    v1,0x10(s0)
+lb    v0,0x10(s0)
"""


def attribution(opcode="lb"):
    return {"status": "verified", "instructions": [
        {"normalized_line": 11, "candidate_line": 7, "instruction": f"{opcode} v0,16(s0)"},
        {"normalized_line": 13, "candidate_line": 9, "instruction": "lb v0,16(s0)"}]}


def test_repeated_rows_are_distinguishable_and_keep_sign_first():
    out = diff_annotate.annotate(DIFF).splitlines()
    rows = [l for l in out if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    assert [l.split("; ")[1] for l in rows] == ["T11", "C11", "T13", "C13"]
    assert all(l[0] in "+-" for l in rows)


def test_c_line_added_when_attributed_and_opcode_agrees():
    out = diff_annotate.annotate(DIFF, attribution())
    assert "; C11 L7" in out and "; C13 L9" in out
    assert "; T11\n" in out and "L" not in out.split("; T11")[1].split("\n")[0]


def test_c_line_omitted_on_opcode_mismatch_or_unverified():
    assert "L7" not in diff_annotate.annotate(DIFF, attribution("lw"))
    bad = {**attribution(), "status": "unavailable"}
    notes = [l.split("; ")[1] for l in diff_annotate.annotate(DIFF, bad).splitlines() if "; " in l]
    assert notes == ["T11", "C11", "T13", "C13"]


def test_empty_and_headerless_diff_unchanged():
    assert diff_annotate.annotate("") == ""
    assert diff_annotate.annotate("-a\n+b\n") == "-a\n+b\n"

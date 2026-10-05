"""compile_fix_prompt puts every error beside its source line and fits large functions in the byte budget."""
from solver import compile_fix_prompt as cfp, edit_slots

SOURCE = """#include "common.h"
extern void *gRegionAllocPtr;

void drawMenuGlyph(s32 arg0) {
    void *temp_v0;
    s32 sp74;

    temp_v0 = gRegionAllocPtr;
    temp_t5 = sp74 + (arg0 * 8);
    gRegionAllocPtr = temp_v0 + 8;
}
"""
IDO = ("cfe: Error: candidate.c, line 9: 'temp_t5' undefined; reoccurrences will not be reported.\n"
       "cfe: Error: candidate.c, line 10: Unacceptable operand of '+'.\n"
       "cfe: Warning: candidate.c, line 4: unused\n")
CLANG = ("candidate.c:9:5: error: use of undeclared identifier 'temp_t5'\n"
         "    9 |     temp_t5 = sp74 + (arg0 * 8);\n"
         "candidate.c:10:31: error: arithmetic on a pointer to void\n")
KINDS = {"declarations", "expression", "other"}


def test_errors_merge_ido_and_clang_by_line_and_skip_warnings():
    found = cfp.errors(IDO, CLANG)
    assert sorted(found) == [9, 10]
    assert found[9][0].startswith("IDO: 'temp_t5' undefined") and found[9][1].startswith("clang col 5:")


def test_fires_with_errors_beside_numbered_source_lines_and_one_copy_of_the_source():
    prompt, report = cfp.build(SOURCE, function="drawMenuGlyph", compiler_stderr=IDO, diagnostics=CLANG,
                               kinds=KINDS, max_edits=8, assembly="glabel drawMenuGlyph\n  jr $ra")
    errors_at = prompt.index("COMPILER ERRORS WITH SOURCE LINES")
    assert ">> L9:     temp_t5 = sp74 + (arg0 * 8);" in prompt[errors_at:]
    assert ">> L10:     gRegionAllocPtr = temp_v0 + 8;" in prompt
    assert "clang col 31: arithmetic on a pointer to void" in prompt
    assert prompt.index("COMPILER ERRORS WITH") < prompt.index("EDITABLE SOURCE (") < prompt.index("TARGET ASSEMBLY (")
    assert prompt.count("gRegionAllocPtr = temp_v0 + 8;") == 2          # error context + slot table, never a third copy
    assert report["error_lines"] == 2 and not report["source_windowed"] and not report["omitted"]
    # Include lines travel with the prompt: the contamination guard's header exemption reads them.
    assert 'INCLUDED HEADERS (read-only context of CURRENT C):\n#include "common.h"' in prompt


def test_large_sources_are_windowed_and_optional_evidence_is_dropped_before_the_budget():
    body = "".join(f"    x{i} = {i};\n" for i in range(3000))
    source = SOURCE.replace("    gRegionAllocPtr = temp_v0 + 8;\n", body + "    gRegionAllocPtr = temp_v0 + 8;\n")
    line = source.splitlines().index("    gRegionAllocPtr = temp_v0 + 8;") + 1
    ido = f"cfe: Error: candidate.c, line {line}: Unacceptable operand of '+'.\n"
    prompt, report = cfp.build(source, function="drawMenuGlyph", compiler_stderr=ido, diagnostics="", kinds=KINDS,
                               max_edits=8, assembly="x" * 30000, storage_packet="y" * 30000, budget_bytes=20000)
    assert report["source_windowed"] and len(prompt.encode()) <= 20000
    assert f"L{line}: " in prompt and "L5:     void *temp_v0;" in prompt and "omitted (still editable by slot)" in prompt
    assert set(report["omitted"]) == {"target assembly", "storage/type reconstruction input"}
    # Omitted slots stay bound: a proposal may still edit a line the window did not show.
    key = next(k for k in edit_slots.slots(source) if k.endswith(":L100"))
    assert edit_slots.apply(source, [type("E", (), {"slot": key, "old": "", "new": "    x96 = 0;"})()]) != source


def test_build_failures_without_line_numbers_are_still_shown():
    stderr = "The C file contains a do-while loop; the build helper requires for(;;)"
    prompt, report = cfp.build(SOURCE, function="drawMenuGlyph", compiler_stderr=stderr, diagnostics="",
                               kinds=KINDS, max_edits=8)
    assert "BUILD FAILURE OUTPUT" in prompt and "do-while" in prompt and report["error_lines"] == 0

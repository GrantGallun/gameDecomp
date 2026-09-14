"""Tests for extract_c, one per failure shape found in the attempts log.

Log mining over 956 non-compiling attempts found 132 (13.8%) were extraction or
truncation faults scored as model errors. Each test below reproduces one of
those shapes using the actual text observed in the database.
"""

from solver import llm

# --- the three observed failure shapes -------------------------------------

TRUNCATED_FENCE = '''Here is the function.

```c
#include "common.h"

void resetAllViewports(void) {
    gViewportStates[0x160] = 0;
    gViewportStates'''

ECHOED_ASM = '''I need to study the target first.

```
/* 1C858 8001BC58 01E1C825 */  or         $t9, $t7, $at
/* 1C860 8001BC60 07010003 */  bgez       $t8, .L8001BC70
/* 1C864 8001BC64 00187B03 */   sra       $t7, $t8, 12
/* 1C868 8001BC68 27010FFF */  addiu      $at, $t8, 0xFFF
/* 1C86C 8001BC6C 00017B03 */  sra        $t7, $at, 12
```
'''

GOOD = '''Reasoning about the shape.

```c
#include "common.h"

void f(s32 a) {
    gThing = a;
}
```'''


def test_truncated_fence_does_not_leak_backtick():
    """53 sources began with a literal ```c because no closing fence existed."""
    out = llm.extract_c(TRUNCATED_FENCE)
    assert not out.startswith("```"), "opening fence leaked into the source"
    assert "```" not in out, f"stray fence survived: {out[:60]!r}"
    assert "#include" in out, "recoverable C was discarded"


def test_echoed_assembly_is_never_returned_as_c():
    """32 stored sources were literally MIPS assembly, chosen by the
    max(len) fallback because echoed asm is always the longest block."""
    out = llm.extract_c(ECHOED_ASM)
    assert "$t9" not in out and "$at" not in out, "assembly returned as C"
    assert out == "", f"expected an explicit non-answer, got {out[:60]!r}"


def test_no_c_at_all_returns_empty_not_garbage():
    """An explicit "" lets the caller log an extraction failure rather than
    booking a harness fault as a model error."""
    assert llm.extract_c("I'm sorry, I can't help with that.") != ""  # prose
    assert llm.extract_c("") == ""
    assert llm.extract_c("```\n\n```") == ""


def test_normal_response_still_works():
    out = llm.extract_c(GOOD)
    assert out.startswith("#include"), out[:40]
    assert "```" not in out
    assert "gThing = a;" in out


def test_last_function_bearing_fence_wins():
    """Reasoning models emit discarded attempts; the answer is the last fence
    that actually defines a function, not the longest one."""
    text = ("```c\n/* a long discarded sketch with no function at all, padded "
            "out so that it is comfortably the longest block in the "
            "response by character count. */\nint x;\n```\n"
            "```c\nvoid real(void) { gX = 1; }\n```")
    out = llm.extract_c(text)
    assert "void real" in out, out[:80]


def test_asm_detector_does_not_reject_real_c():
    """A false positive here would silently discard valid answers, which is
    worse than the bug being fixed."""
    c = ('#include "common.h"\n'
         "void f(void) {\n"
         "    s32 a = 1;\n"
         "    s32 b = a + 2;\n"
         "    gOut = b;\n"
         "}\n")
    assert not llm._looks_like_asm(c)
    assert llm.extract_c("```c\n" + c + "```") != ""


def test_classify_extraction_names_the_failure_mode():
    """A bare "it failed" is unactionable; the mode decides the fix."""
    assert llm.classify_extraction(GOOD, llm.extract_c(GOOD)) == "ok"
    assert llm.classify_extraction(ECHOED_ASM,
                                   llm.extract_c(ECHOED_ASM)) == "empty"
    assert llm.classify_extraction("I'm sorry, I can't provide that.",
                                   "") == "refusal"
    trunc = "```c\nvoid f(void) {\n  int x = 1;"
    assert llm.classify_extraction(trunc, llm.extract_c(trunc)) in (
        "unterminated", "ok")


def test_refusals_are_recognised_before_they_reach_the_compiler():
    """84 of 157 attempts in a real eval run stored refusal prose as their
    "source", and every one was compiled. The syntax error then counted as a
    model failure and pulled the reported mean down."""
    for txt in ["I'm sorry, but I can't provide that.",
                "I’m sorry, but I can’t provide that.",
                "I cannot assist with that request.",
                "Sorry -- I am unable to provide this."]:
        assert llm.is_refusal(txt), txt


def test_real_c_is_not_mistaken_for_a_refusal():
    """A false positive here silently discards valid work, which is worse than
    the bug being fixed."""
    c = ('#include "common.h"\n'
         "/* the caller cannot pass NULL here */\n"
         "void f(void) { gX = 1; }\n")
    assert not llm.is_refusal(c)
    assert not llm.is_refusal("void cannotFail(void) { }")


def test_refusal_check_only_looks_at_the_head():
    """Real C may say "cannot" in a comment far down; that is not a refusal."""
    body = "void f(void){ gX=1; }\n" * 40 + "/* this cannot be reached */\n"
    assert not llm.is_refusal(body)

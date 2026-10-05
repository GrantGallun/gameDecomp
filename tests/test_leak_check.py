"""tools/leak_check.py must FIRE on copies (verbatim, renamed, partial) and stay quiet on unrelated code."""
from tools import leak_check as lc

ANSWER = """s32 mario_add_coins(MarioState *m, s32 n)
{
  s32 total;
  total = m->coins + n;
  if (total > 0x64) { play_sound(total, &m->health); m->coins = 0; }
  return total;
}
"""


def test_a_verbatim_copy_on_a_page_is_a_leak():
    page = "<html><pre>// src/game/mario.c\n" + ANSWER + "</pre> more text</html>"
    v = lc.leak([ANSWER], page)
    assert v["leak"] and v["raw"] > 0


def test_a_renamed_copy_is_a_leak():
    renamed = ANSWER.replace("mario_add_coins", "func_80012340").replace("total", "sp1C").replace("coins", "unk_3C")
    v = lc.leak([ANSWER], renamed)
    assert v["leak"] and v["structural"] > 0


def test_unrelated_code_is_not_a_leak():
    other = """void osInitialize(void) { u32 pifdata; __osFinalrom = 1; if (osResetType == 0) { return; } }
    s32 sum(s32 *a, s32 n) { s32 i; s32 s = 0; for (i = 0; i < n; i++) { s += a[i]; } return s; }"""
    assert not lc.leak([ANSWER], other)["leak"]


def test_both_spellings_of_the_answer_are_checked():
    anonymized = ANSWER.replace("mario_add_coins", "func_80012340")
    assert lc.leak([anonymized, ANSWER], ANSWER)["leak"]


def test_idioms_shared_by_other_functions_do_not_count():
    idiom = "if (ptr == 0) { return; } gDisplayListHead = gDisplayListHead + 8; gSPEndDisplayList(gDisplayListHead);"
    answer = "void a(void) { " + idiom + " }"
    page = "void b(void) { " + idiom + " }"
    common = lc.common_grams([answer, "void c(void) { " + idiom + " }"])
    assert lc.leak([answer], page)["raw"] > 0                   # without the background: looks like a copy
    assert lc.leak([answer], page, common)["raw"] == 0           # with it: a shared idiom, not this answer

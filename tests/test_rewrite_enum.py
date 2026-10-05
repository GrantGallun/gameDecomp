"""Rewrite generation (eval.rewrite_enum): shapes, semantic fingerprints, sibling pruning, IDO probes."""
import pytest

from eval import rewrite_enum as ren
from solver import term_rewrite as tr

ENV = ren.samples()


def fp(text):
    return ren.fingerprint(tr.pattern(text), ENV)


@pytest.mark.parametrize("a,b", [
    ("E0 * 2", "E0 << 1"), ("E0 * 2", "E0 + E0"), ("!(E0 == E1)", "E0 != E1"), ("E0 - E1 == 0", "E0 == E1"),
    ("E0 + E1", "E1 + E0"), ("(s16)E0", "(s32)(s16)E0"), ("E0 != 0", "!!E0"), ("E0 < E1", "E1 > E0"),
    ("-E0", "0 - E0"), ("~E0", "-E0 - 1"), ("E0 && E1", "E1 && E0"), ("E0 == N0", "N0 == E0")])
def test_equal_spellings_share_a_fingerprint(a, b):
    assert fp(a) == fp(b)


@pytest.mark.parametrize("a,b", [
    ("E0 / 2", "E0 >> 1"),                  # rounds differently for negative signed values
    ("(u8)E0", "E0 & 0xFF"),                # int vs unsigned result when E0 is unsigned
    ("E0 & 0xFF", "E0 % 256"), ("E0 << 16 >> 16", "(s16)E0"), ("E0 - 1 < 0", "E0 < 1"),
    ("E0 == N0", "E0 == 0"),                # needs rows where the operand equals the literal
    ("E0 == 2", "(u8)E0 == 2"),             # needs values that agree only in the low byte
    ("E0 == 16", "16 == (s16)E0")])
def test_different_meanings_are_told_apart(a, b):
    assert fp(a) != fp(b)


def test_undefined_inputs_must_agree():
    assert fp("E0 / E1") != fp("E0 * E1")
    assert fp("E0 / E1") == fp("(E0 / E1)")
    assert ren.fingerprint(tr.pattern("E0 / 0"), ENV) is None


def test_shapes_fire_on_their_motivating_source():
    src = "s32 k(s32 x, s32 *p) {\n    if (x == 0) { return p[1] + 24; }\n    return (s16)(x - 1);\n}\n"
    b, e = src.index("{") + 1, src.rindex("}")
    found = set()
    for t in tr.body_trees(src, b, e):
        for n in tr.walk(t):
            if n.kind in ("bin", "un", "cast"):
                found |= {ren.number(raw) for raw, ops in ren.abstractions(n, src) if ops}
    assert {"E0 == 0", "E0 + 24", "(s16)(E0 - 1)", "E0 - 1", "(s16)E0"} <= found


def test_padding_and_split_constants_are_pruned():
    assert ren.padded(tr.pattern("E0 + 0"), ENV)
    assert ren.padded(tr.pattern("(s32)(s32)E0"), ENV)
    assert ren.padded(tr.pattern("(2 || E0) + E0"), ENV)
    assert not ren.padded(tr.pattern("E1 + E0"), ENV)
    assert not ren.padded(tr.pattern("!E0"), ENV)
    assert ren.literals(tr.pattern("16 + E0 + 8")) > ren.literals(tr.pattern("E0 + 24"))
    assert ren.uses(tr.pattern("E0 + E0 == E0"))["E0"] == 3


@pytest.mark.skipif(not ren.REPO.exists(), reason="needs the IDO toolchain (WSL)")
def test_probe_tells_compiled_spellings_apart():
    from tools import synthetic_corpus
    recipe = synthetic_corpus.recipe(ren.REPO)
    work = [("pa_ret", "E0 + E1", "ret"), ("pb_ret", "E1 + E0", "ret"),
            ("pc_ret", "E0 * 2", "ret"), ("pd_ret", "E0 << 1", "ret")]
    got = ren._compile_batch((work, recipe))["listings"]
    assert got["pa_ret"] != got["pb_ret"]          # operand order survives into addu
    assert got["pc_ret"] == got["pd_ret"]          # IDO strength-reduces the multiply: inert

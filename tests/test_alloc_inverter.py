"""solver.alloc_inverter: fires on a blocked range at a site where the empty test is a read, declines elsewhere."""
from types import SimpleNamespace

from solver import alloc_inverter

SOURCE = """void f(int n) {
    int a;
    int b;

    a = g(n);
    b = h(n);
    if (n) {
        a = k(n);
    }
    b = 5;
    a = n;
    use(a, b);
}
"""


def _range(offset, colour, adjsave, blocks):
    return SimpleNamespace(kind="M", offset=offset, color=colour, adjsave=adjsave,
                           blocks=[(b,) for b in blocks], default_blocks=[])


def _proc(outcome="constrained"):
    # b (lr 2, offset -8) holds s0 (colour 14) and was coloured first; a (lr 1, offset -4) got s1 and wants s0
    ranges = {1: _range(-4, 15, 2.0, [1, 2]), 2: _range(-8, 14, 3.0, [1, 2])}
    decisions = [SimpleNamespace(piece=2, outcome=outcome), SimpleNamespace(piece=1, outcome=outcome)]
    return SimpleNamespace(ranges=ranges, decisions=decisions)


def _report(cls):
    return {"ranges": [{"lr": 1, "class": cls, "desired": "s0"}]}


def test_raise_fires_on_blocked_range_after_a_call_result():
    out = alloc_inverter.propose(SOURCE, "f", _report("blocked"), _proc())
    labels = [label for label, _ in out]
    assert labels and all(label.startswith("raise:a+") for label in labels)
    sites = {SOURCE.index(t) + len(t) for t in ("a = g(n);", "a = k(n);")}      # not `a = n;` (a copy)
    assert {int(label.rsplit("@", 1)[1]) for label in labels} == sites
    assert "a = g(n);\n    if (!a);" in out[0][1]


def test_raise_skips_constant_and_local_copy_sites_only():
    at = lambda text: SOURCE.index(text) + len(text)
    assert not alloc_inverter._read_counts(SOURCE, "f", "a", at("a = n;"))       # copy of a parameter
    assert not alloc_inverter._read_counts(SOURCE, "f", "b", at("b = 5;"))       # constant
    assert alloc_inverter._read_counts(SOURCE, "f", "b", at("b = h(n);"))
    assert alloc_inverter._read_counts(SOURCE, "f", "a", at("a = k(n);"))        # in an arm: not gated
    # a global is a load, not a copy (func_80058C00: `temp_v0 = gRegionAllocPtr;` performed under v1)
    src = "void f(int n) {\n    int t;\n\n    t = gRegionAllocPtr;\n    use(t);\n}\n"
    assert alloc_inverter._read_counts(src, "f", "t", src.index("gRegionAllocPtr;") + len("gRegionAllocPtr;"))


def test_raise_declines_on_selection_class():
    assert alloc_inverter.propose(SOURCE, "f", _report("selection"), _proc()) == []


def test_loop_body_site_still_counts():
    src = "void f(int n) {\n    int a;\n\n    do {\n        a += g(n);\n    } while (a < n);\n}\n"
    site = src.index("a += g(n);") + len("a += g(n);")
    assert alloc_inverter._read_counts(src, "f", "a", site)

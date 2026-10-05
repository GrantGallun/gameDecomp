"""The equivalence miner fires on its motivating case and refuses what it must refuse."""
from __future__ import annotations

from patterns import equivalences as eq


def fn(name: str, body: str) -> str:
    return f'#include "common.h"\nvoid {name}(s16 *arr, s32 i) {{\n{body}\n}}\n'


def edge(eid, name, before_body, after_body, same, tu="tu_a"):
    return (eid, name, tu, fn(name, before_body), fn(name, after_body), same)


INDEX_TO_POINTER = [
    edge(1, "f1", "    arr[i] = 0;", "    *(arr + i) = 0;", True),
    edge(2, "f2", "    arr[i] = 0;", "    *(arr + i) = 0;", True),
    edge(3, "f3", "    arr[i] = 0;", "    *(arr + i) = 0;", True),
    edge(4, "f3", "    *(arr + i) = 0;", "    arr[i] = 0;", True),       # other direction
    edge(5, "f4", "    arr[i] = 0;", "    *(arr + i) = 0;", True),
]


def test_abstraction_is_consistent_and_direction_free() -> None:
    forward = eq.edit_of(fn("f", "    arr[i] = 0;"), fn("f", "    *(arr + i) = 0;"))
    backward = eq.edit_of(fn("g", "    *(arr + i) = 0;"), fn("g", "    arr[i] = 0;"))
    renamed = eq.edit_of(fn("h", "    tbl[k] = 0;"), fn("h", "    *(tbl + k) = 0;"))
    assert forward.key == backward.key == renamed.key
    assert "I0" in forward.render() and "arr" not in forward.render()
    unchanged_value = eq.edit_of(fn("f", "    arr[i] = 1;"), fn("f", "    *(arr + i) = 1;"))
    assert unchanged_value.key == forward.key, "a constant outside the edit is not part of it"
    one = eq.edit_of(fn("f", "    i = i + 1;"), fn("f", "    i = 1 + i;"))
    seven = eq.edit_of(fn("f", "    i = i + 7;"), fn("f", "    i = 7 + i;"))
    eight = eq.edit_of(fn("f", "    i = i + 8;"), fn("f", "    i = 8 + i;"))
    assert one.key != seven.key, "0 and 1 stay literal inside an edit: they change instruction selection"
    assert seven.key == eight.key, "other constants abstract to placeholders"


def test_mask_and_shift_constants_stay_literal() -> None:
    """The first held-out run's wrong skips: `x & 0xFF` <=> `x` abstracted to `N0 & N1` <=> `N0`,
    which is a no-op only when the operand is already that wide."""
    ff = eq.edit_of(fn("f", "    i = i & 0xFF;"), fn("f", "    i = i;"))
    ffff = eq.edit_of(fn("f", "    i = i & 0xFFFF;"), fn("f", "    i = i;"))
    assert ff.key != ffff.key and "0xFF" in ff.render()
    shift3 = eq.edit_of(fn("f", "    i = i << 3;"), fn("f", "    i = i * 8;"))
    shift4 = eq.edit_of(fn("f", "    i = i << 4;"), fn("f", "    i = i * 16;"))
    assert shift3.key != shift4.key


def test_render_reads_as_two_spellings() -> None:
    edit = eq.edit_of(fn("f", "    i = (i + 4) * 2;"), fn("f", "    i = (4 + i) * 2;"))
    left, right = edit.render().split("  <=>  ")
    assert "I0" in left and "I0" in right and "<=>" not in left


def test_miner_fires_on_the_motivating_equivalence() -> None:
    mined = eq.mine(INDEX_TO_POINTER)
    rules = eq.rules(mined, min_noop=5, min_functions=3)
    assert rules[0]["class"] == "equivalence" and rules[0]["noop"] == 5
    assert rules[0]["functions"] == 4 and rules[0]["examples"][:2] == [1, 2]
    index = eq.Index(rules)
    assert index.predicts_noop(fn("z", "    q[j] = 0;"), fn("z", "    *(q + j) = 0;")) is not None
    assert index.predicts_noop(fn("z", "    q[j] = 0;"), fn("z", "    q[j] = 5;")) is None
    assert rules[0]["pattern"] in index.prompt_block()


def test_one_object_change_makes_a_pattern_context_dependent() -> None:
    mined = eq.mine(INDEX_TO_POINTER + [edge(6, "f5", "    arr[i] = 0;", "    *(arr + i) = 0;", False)])
    rule = eq.rules(mined)[0]
    assert rule["class"] == "context-dependent" and rule["counterexamples"] == [6]
    assert eq.Index(eq.rules(mined)).rules == {}, "context-dependent patterns never skip"


def test_a_safe_looking_member_of_an_unsafe_family_is_not_confirmed() -> None:
    """Declaration reordering is a register lever: one shape inert everywhere it was seen does not
    make the family safe when other shapes of it move code."""
    safe_shape = [edge(20 + k, f"d{k}", "    s32 a;\n    u8 *b;", "    u8 *b;\n    s32 a;", True)
                  for k in range(6)]
    other_shape = [edge(40 + k, f"e{k}", "    s32 a;\n    s16 b;", "    s16 b;\n    s32 a;", False)
                   for k in range(3)]
    rules = eq.rules(eq.mine(safe_shape + other_shape), min_noop=5, min_functions=3)
    safe = next(r for r in rules if r["noop"] == 6)
    assert safe["family"] == "decl-reorder" and safe["class"] == "family-context-dependent"
    assert eq.Index(rules).rules == {}
    # The motivating index/pointer equivalence is not a reordering and stays confirmed.
    assert eq.family(eq.edit_of(fn("f", "    arr[i] = 0;"), fn("f", "    *(arr + i) = 0;"))) is None
    const = eq.edit_of(fn("f", "    i = (i * 3) + 8;"), fn("f", "    i = 8 + (i * 3);"))
    var = eq.edit_of(fn("f", "    i = (i * 3) + arr;"), fn("f", "    i = arr + (i * 3);"))
    assert eq.family(const) == "compound-commute:const"
    assert eq.family(var) == "compound-commute:var"
    swapped_args = eq.edit_of(fn("f", "    g((i * 3), 8);"), fn("f", "    g(8, (i * 3));"))
    assert eq.family(swapped_args) == "swap", "argument order is a different program, not commutation"
    minus = eq.edit_of(fn("f", "    i = (i * 3) - 8;"), fn("f", "    i = 8 - (i * 3);"))
    assert eq.family(minus) == "swap", "subtraction does not commute"


def test_identifier_swaps_are_never_confirmed() -> None:
    """Clean statistics from prototypes do not make a local renaming sound in a definition."""
    rows = [(60 + k, f"p{k}", "tu_a", f"void f{k}(s32 a, s32 b, s32 c);\n",
             f"void f{k}(s32 b, s32 a, s32 c);\n", True) for k in range(8)]
    rules = eq.rules(eq.mine(rows), min_noop=5, min_functions=3)
    assert rules[0]["family"] == "identifier-swap" and rules[0]["noop"] == 8
    assert rules[0]["class"] == "family-context-dependent" and eq.Index(rules).rules == {}


def test_thin_support_is_only_a_candidate() -> None:
    rules = eq.rules(eq.mine(INDEX_TO_POINTER[:2]), min_noop=5, min_functions=3)
    assert rules[0]["class"] == "equivalence-candidate"
    assert eq.Index(rules).rules == {}


def test_non_local_edits_are_not_mined() -> None:
    keep = "\n    x = y + z;\n    y = x - z;\n"
    three_places = eq.edit_of(fn("f", "    a = 1;" + keep + "    c = 3;" + keep + "    d = 4;"),
                              fn("f", "    a = 9;" + keep + "    c = 8;" + keep + "    d = 7;"))
    assert three_places is None, "more than two separated hunks is not a local spelling change"
    adjacent = eq.edit_of(fn("f", "    c = 3;\n    d = 4;"), fn("f", "    c = 8;\n    d = 7;"))
    assert adjacent is not None and len(adjacent.hunks) == 1, "adjacent changes are one hunk"
    assert eq.edit_of(fn("f", "    a = 1;"), fn("f", "    a = 1;")) is None
    assert eq.edit_of(fn("f", "    a = 1; /* x */"), fn("f", "    a = 1; /* y */")) is None, \
        "a comment is not a token"


def test_split_mining_and_held_out_evaluation() -> None:
    rows = INDEX_TO_POINTER + [
        edge(10, "g1", "    arr[i] = 0;", "    *(arr + i) = 0;", True, tu="tu_b"),
        edge(11, "g2", "    arr[i] = 0;", "    *(arr + i) = 0;", False, tu="tu_b"),   # a miss
        edge(12, "g3", "    i = i + 1;", "    i += 1;", True, tu="tu_b"),              # no rule
    ]
    split = lambda tu, fn_name: "train" if tu == "tu_a" else "test"
    train = eq.rules(eq.mine(rows, split_of=split, keep_split="train"))
    held = [r for r in rows if split(r[2], r[1]) == "test"]
    report = eq.evaluate(train, held)
    assert report["held_out_edges"] == 3 and report["held_out_noops"] == 2
    assert report["would_skip"] == 2 and report["precision"] == 0.5
    assert report["recall"] == 0.5 and report["wrong_skips"][0]["edge"] == 11

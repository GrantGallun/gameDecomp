"""Which recovered layouts get inserted, and why the rest do not.

THE GAP THIS PINS. `opaque_variant` asked the headers for a tag, found none, and moved on -- silently. With
`UnseenActor *arg0` dereferenced at 0xC the planner returns

    typedef struct { char pad00[0xc]; s32 unkC; } UnseenActor;

which is valid C, uses the offsets the binary actually touches, and names the type the source already
spells -- and the action discarded it, while repairing the equivalent `void *` case. The header lookup is
for COMPLETING A TAG THE HEADERS DECLARED, not a precondition for declaring anything at all.

THE OTHER HALF IS OBSERVABILITY. Three different things used to leave the same trace (nothing): the type was
already complete, the field mapping was ambiguous, the access views overlapped. Every plan now ends in
exactly one of accepted / derived / declined, and every decline carries a reason that reaches the receipt.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import compile_obligations, typedecl                              # noqa: E402

FUNCTION = "probe"
LAYOUT = {"param0": [(12, 4, "s32")]}
ORPHAN = "void probe(UnseenActor *arg0) {\n    arg0->unkC = 1;\n}\n"
UNTYPED = "void probe(void *arg0) {\n    arg0->unkC = 1;\n}\n"
HEADER_COMPLETE = "typedef struct RacePlayer {\n    /* 0x0 */ s32 speed;\n} RacePlayer;"


def _decide(source, layout=None, header="", forward=None, bare=(), function=FUNCTION):
    layout = LAYOUT if layout is None else layout
    plans = typedecl.plan(source, function, layout, set())
    return (*compile_obligations.plan_declarations(source, function, plans, layout, header,
                                                   dict(forward or {}), set(bare)), plans)


def test_a_type_no_header_supplies_is_declared_instead_of_discarded():
    """THE MOTIVATING CASE. It used to be thrown away because `forward.get('UnseenActor')` was None."""
    accepted, derived, declined, plans = _decide(ORPHAN)
    assert len(plans) == 1 and plans[0]["type"] == "UnseenActor"
    assert len(accepted) == 1 and not derived and not declined
    entry = accepted[0]
    assert entry.get("orphan") is True
    # The declaration names the type the SOURCE uses and the offsets the binary touches.
    assert entry["text"].startswith("typedef struct {")
    assert entry["text"].rstrip().endswith("} UnseenActor;")
    assert "char pad00[0xc];" in entry["text"] and "s32 unkC;" in entry["text"]


def test_a_void_parameter_still_gets_a_derived_tag_and_a_respelling():
    """The half that already worked, kept working: `} void;` is not valid C, so the type has to be named."""
    accepted, derived, declined, _ = _decide(UNTYPED)
    assert accepted == [] and declined == []
    tag, variable, text = derived[0]
    assert tag == "probe_arg0" and variable == "arg0"
    assert text.startswith("struct probe_arg0 {") and text.rstrip().endswith("};")


def test_a_type_the_header_already_completes_is_declined_with_a_reason():
    """Not silently skipped: the receipt says the members were already visible."""
    accepted, derived, declined, _ = _decide(
        "void probe(RacePlayer *arg0) {\n    arg0->speed = 1;\n}\n",
        layout={"param0": [(0, 4, "s32")]}, header=HEADER_COMPLETE,
        forward={"RacePlayer": "RacePlayer"})
    assert accepted == []
    assert [entry["reason"] for entry in declined] == ["the header already defines this type"]
    assert declined[0]["tag"] == "RacePlayer" and declined[0]["limitation"]


def test_an_ambiguous_field_mapping_is_declined_with_the_names_it_could_not_place():
    source = "void probe(UnseenActor *arg0) {\n    arg0->alpha = arg0->beta;\n}\n"
    accepted, derived, declined, _ = _decide(source, layout={"param0": [(0, 4, "s32"), (4, 4, "s32")]})
    assert accepted == []
    assert declined[0]["reason"] == "ambiguous field-name mapping"
    assert sorted(declined[0]["source_members"]) == ["alpha", "beta"]
    assert declined[0]["observed_parameter_slots"]["0"], "the offsets it did have are in the receipt"


def test_overlapping_access_views_are_declined_with_the_offsets():
    source = "void probe(UnseenActor *arg0) {\n    arg0->unk4 = 1;\n}\n"
    accepted, derived, declined, _ = _decide(source, layout={"param0": [(4, 4, "s32"), (6, 2, "s16")]})
    assert accepted == [] and declined[0]["reason"] == "overlapping access views"
    assert declined[0]["observed"] == [[4, 4, "s32"], [6, 2, "s16"]]


def test_every_plan_ends_in_exactly_one_bucket():
    """The completeness property the silent declines violated: a plan cannot vanish. Whatever is not
    accepted is either derived or declined, and nothing else is possible."""
    source = ("void probe(UnseenActor *arg0, void *arg1) {\n"
              "    arg0->alpha = arg0->beta;\n"
              "    arg1->unkC = 1;\n"
              "}\n")
    layout = {"param0": [(0, 4, "s32"), (4, 4, "s32")], "param1": [(12, 4, "s32")]}
    plans = typedecl.plan(source, FUNCTION, layout, set())
    accepted, derived, declined, _ = _decide(source, layout=layout)
    void_plans = [p for p in plans if p["type"] == "void"]
    assert len(accepted) + len(derived) + len(declined) == len(plans), \
        (len(accepted), len(derived), len(declined), len(plans))
    assert len(derived) == len(void_plans) == 1
    assert declined and declined[0]["reason"] == "ambiguous field-name mapping"


def test_the_orphan_declaration_is_valid_c_even_though_the_name_is_invented():
    """A declared type whose body comes from the binary, not from a header: the acceptance test is that it
    compiles, which the object certificate decides elsewhere. Here the shape is checked."""
    accepted, _, _, _ = _decide(ORPHAN)
    text = accepted[0]["text"]
    assert text.count("{") == 1 and text.count("}") == 1 and text.endswith(";\n") is False
    assert "typedef struct {" in text and "} UnseenActor;" in text


def test_a_base_type_is_never_declared_as_a_struct():
    """`typedecl.plan` will plan for a parameter written `s32 *arg0` and dereferenced, and
    `typedef struct { ... } s32;` is not a repair. Letting it through cost a compiling state
    (`osEPiRawReadIo`, 79.15 -> 0.0) because the collision guard then abandoned the whole action."""
    accepted, derived, declined, _ = _decide("void probe(s32 *arg0) {\n    arg0->unkC = 1;\n}\n")
    assert accepted == []
    assert [entry["reason"] for entry in declined] == [
        "the name is already a type in this translation unit"]


def test_a_collision_costs_only_its_own_declaration():
    """PER DECLARATION, NOT ALL-OR-NOTHING. One unusable plan must not take the good ones with it."""
    added = [{"type": "s32", "text": "typedef struct {\n    s32 a;\n} s32;"},
             {"type": "Good", "text": "typedef struct {\n    s32 b;\n} Good;"}]
    blocked = compile_obligations.redeclarations(added, "void f(void) {}\n",
                                                 "typedef signed int s32;")
    assert len(blocked) == 1 and blocked[0]["type"] == "s32"
    assert "redeclare the type name 's32'" in blocked[0]["reason"]
    # The old single-answer shape still answers, for callers written against it.
    assert "s32" in compile_obligations._redeclaration(added, "void f(void) {}\n",
                                                       "typedef signed int s32;")


def test_a_source_level_struct_tag_is_projected_so_the_planner_can_see_it():
    """`struct Unseen *arg0` with nothing forward-declaring `struct Unseen` gave the planner zero plans: it
    keys on typedef-shaped names. The projection is the same mechanism the headers already used, and it is
    confined to the planner's private input."""
    source = "void probe(struct Unseen *arg0) {\n    arg0->unkC = 1;\n}\n"
    assert typedecl.plan(source, FUNCTION, LAYOUT, set()) == []
    projected = source.replace("struct Unseen *", "Unseen *")
    assert len(typedecl.plan(projected, FUNCTION, LAYOUT, set())) == 1

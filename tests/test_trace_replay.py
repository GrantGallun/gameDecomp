"""Recorded-call replay: attribution, the soundness gate, and that divergences FIRE.

A synthetic recording in the exact Project64 event format stands in for a real
session. The fire tests are the point: a replay that says "passed" on a wrong
field offset or a wrong argument looks exactly like a replay with nothing to say.
"""
import json
from types import SimpleNamespace

import pytest

from solver import trace_replay

ENTRY = 0x80001000
CALLEE = 0x80002000
A0, SP, RA = 0x80100000, 0x80200000, 0x80003000
TYPES = trace_replay.VERIFIED_TYPE_IDS

ORIGINAL = """
    addiu sp,sp,-0x18
    sw ra,0x14(sp)
    lw t0,4(a0)
    sw t0,8(a0)
    addiu a1,a0,0x10
    jal callee
    nop
    lw ra,0x14(sp)
    jr ra
    addiu sp,sp,0x18
"""
SIZE = 4 * 10


def gpr(**values):
    order = trace_replay.REGISTER_ORDER
    return [values.get(name, 0) for name in order]


def record(*, read_value=0x1234, written_value=0x1234, v0=0x55, extra_events=(), type_ids=None):
    frame = SP - 0x18
    events = [
        ["w", ENTRY + 4, frame + 0x14, TYPES["u32"], RA, None],
        ["r", ENTRY + 8, A0 + 4, TYPES["u32"], read_value, None],
        ["w", ENTRY + 12, A0 + 8, TYPES["u32"], written_value, None],
        ["c", ENTRY + 0x14, CALLEE],
        ["e", CALLEE, A0, A0 + 0x10, 0, 0, frame],
        ["w", CALLEE + 4, A0 + 0x20, TYPES["u8"], 7, None],
        ["x", ENTRY + 0x1C, v0, 0],
        ["r", ENTRY + 0x1C, frame + 0x14, TYPES["u32"], RA, None],
        *extra_events,
    ]
    row = {"schema_version": 1, "kind": "project64-call-trace", "function": "probe",
           "entry_address": ENTRY, "end_address": ENTRY + SIZE, "index": 0, "entry_ordinal": 1,
           "entry": {"gpr": gpr(a0=A0, sp=SP, ra=RA)}, "exit": {"gpr": gpr(v0=v0, sp=SP, ra=RA)},
           "events": events, "truncated": False}
    if type_ids is not None:
        row["type_ids"] = type_ids
    return row


def run(candidate=ORIGINAL, rec=None):
    return trace_replay.replay(rec or record(), ORIGINAL, candidate, entry=ENTRY, size=SIZE,
                               arities={"callee": 2}, return_registers=("v0",),
                               symbol_map={"callee": CALLEE})


def test_decode_attributes_body_callee_and_foreign_accesses():
    rec = record(extra_events=[["w", 0x80000180, 0x80300000, TYPES["u16"], 1, None]])
    decoded = trace_replay.decode(rec, ENTRY, ENTRY + SIZE)
    assert [a for _pc, a, _raw in decoded.body_writes] == [SP - 0x18 + 0x14, A0 + 8]
    assert decoded.calls[0].arguments == (A0, A0 + 0x10, 0, 0)
    assert decoded.calls[0].writes == [(A0 + 0x20, b"\x07")]
    assert decoded.calls[0].v0 == 0x55
    assert decoded.foreign_accesses == 1
    # Read before any write: part of starting memory, big-endian.
    assert [decoded.initial[A0 + 4 + i] for i in range(4)] == [0, 0, 0x12, 0x34]
    # Read after the body wrote it: not starting memory.
    assert SP - 0x18 + 0x14 not in decoded.initial


def test_recorded_type_ids_take_precedence_over_verified_order():
    swapped = {**TYPES, "u8": 2, "u32": 0}
    rec = record()
    for event in rec["events"]:
        if event[0] in "rw":
            event[3] = {TYPES["u32"]: 0, TYPES["u8"]: 2}[event[3]]
    rec["type_ids"] = swapped
    decoded = trace_replay.decode(rec, ENTRY, ENTRY + SIZE)
    assert decoded.calls[0].writes == [(A0 + 0x20, b"\x07")]
    assert len(decoded.body_writes[1][2]) == 4


def test_float_values_encode_by_type():
    assert trace_replay._encode("f32", 1.5, None) == bytes.fromhex("3fc00000")
    assert trace_replay._encode("u64", 2, 1) == bytes.fromhex("0000000100000002")
    assert trace_replay._encode("s16", -1, None) == b"\xff\xff"


def test_original_against_itself_passes():
    report = run()
    assert report["status"] == "passed", report


def test_wrong_field_offset_fires_as_write_divergence():
    report = run(ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)"))
    assert report["status"] == "failed"
    assert report["divergence"]["kind"] == "write"
    assert report["divergence"]["target"]["at"] == "a0+0x8"
    assert report["divergence"]["candidate"]["at"] == "a0+0xc"
    assert "wrong field offset" in trace_replay.sentence(report["divergence"])


def test_wrong_pointer_argument_fires_relative_to_entry_register():
    report = run(ORIGINAL.replace("addiu a1,a0,0x10", "addiu a1,a0,0x14"))
    assert report["status"] == "failed"
    divergence = report["divergence"]
    assert divergence["kind"] == "call" and divergence["differing_arguments"] == [1]
    text = trace_replay.sentence(divergence)
    assert "argument 2 is a0+0x10 in the original but a0+0x14 in the candidate" in text


def test_every_differing_call_is_listed_so_the_layout_pattern_shows():
    twice = """
        addiu sp,sp,-0x20
        sw ra,0x1c(sp)
        sw s0,0x18(sp)
        move s0,a0
        addiu a1,s0,0x10
        jal callee
        move a0,s0
        addiu a1,s0,0x14
        jal callee
        move a0,s0
        lw ra,0x1c(sp)
        lw s0,0x18(sp)
        jr ra
        addiu sp,sp,0x20
    """
    size, frame = 14 * 4, SP - 0x20
    rec = record()
    rec["end_address"] = ENTRY + size
    rec["events"] = [
        ["w", ENTRY + 4, frame + 0x1C, TYPES["u32"], RA, None],
        ["w", ENTRY + 8, frame + 0x18, TYPES["u32"], 0, None],
        ["c", ENTRY + 0x14, CALLEE], ["e", CALLEE, A0, A0 + 0x10, 0, 0, frame], ["x", ENTRY + 0x1C, 1, 0],
        ["c", ENTRY + 0x20, CALLEE], ["e", CALLEE, A0, A0 + 0x14, 0, 0, frame], ["x", ENTRY + 0x28, 0x55, 0],
        ["r", ENTRY + 0x28, frame + 0x1C, TYPES["u32"], RA, None],
        ["r", ENTRY + 0x2C, frame + 0x18, TYPES["u32"], 0, None],
    ]
    wrong = twice.replace("addiu a1,s0,0x10", "addiu a1,s0,0x18").replace("addiu a1,s0,0x14", "addiu a1,s0,0x1c")
    report = trace_replay.replay(rec, twice, wrong, entry=ENTRY, size=size, arities={"callee": 2},
                                 return_registers=("v0",), symbol_map={"callee": CALLEE})
    assert report["status"] == "failed"
    later = report["divergence"]["later_differing_calls"]
    assert [row["ordinal"] for row in later] == [1]
    text = trace_replay.sentence(report["divergence"])
    assert "a0+0x10 in the original but a0+0x18" in text and "a0+0x14 in the original but a0+0x1c" in text


TWO_CALLS = """
    addiu sp,sp,-0x20
    sw ra,0x1c(sp)
    sw s0,0x18(sp)
    move s0,a0
    addiu a1,s0,0x10
    jal callee
    move a0,s0
    addiu a1,s0,0x14
    jal callee
    move a0,s0
    lw ra,0x1c(sp)
    lw s0,0x18(sp)
    jr ra
    addiu sp,sp,0x20
"""


def two_call_record():
    frame, rec = SP - 0x20, record()
    rec["end_address"] = ENTRY + 14 * 4
    rec["events"] = [
        ["w", ENTRY + 4, frame + 0x1C, TYPES["u32"], RA, None],
        ["w", ENTRY + 8, frame + 0x18, TYPES["u32"], 0, None],
        ["c", ENTRY + 0x14, CALLEE], ["e", CALLEE, A0, A0 + 0x10, 0, 0, frame], ["x", ENTRY + 0x1C, 1, 0],
        ["c", ENTRY + 0x20, CALLEE], ["e", CALLEE, A0, A0 + 0x14, 0, 0, frame], ["x", ENTRY + 0x28, 0x55, 0],
        ["r", ENTRY + 0x28, frame + 0x1C, TYPES["u32"], RA, None],
        ["r", ENTRY + 0x2C, frame + 0x18, TYPES["u32"], 0, None],
    ]
    return rec


def two_call_replay(candidate):
    return trace_replay.replay(two_call_record(), TWO_CALLS, candidate, entry=ENTRY, size=14 * 4,
                               arities={"callee": 2}, return_registers=("v0",), symbol_map={"callee": CALLEE})


def test_distance_gives_partial_credit_for_half_a_repair():
    both_wrong = TWO_CALLS.replace("a1,s0,0x10", "a1,s0,0x18").replace("a1,s0,0x14", "a1,s0,0x1c")
    one_wrong = TWO_CALLS.replace("a1,s0,0x14", "a1,s0,0x1c")
    assert two_call_replay(TWO_CALLS)["distance"] == 0
    assert two_call_replay(both_wrong)["distance"] == 2
    assert two_call_replay(one_wrong)["distance"] == 1


def test_offset_constraints_fire_for_arguments_and_same_value_stores():
    report = two_call_replay(TWO_CALLS.replace("a1,s0,0x10", "a1,s0,0x18"))
    assert report["offset_constraints"] == [{"kind": "argument", "where": "call #0 callee argument 2",
                                             "register": "a0", "candidate_offset": 0x18, "target_offset": 0x10}]
    store = run(ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)"))
    assert store["offset_constraints"] == [{"kind": "write", "where": "write #0", "register": "a0",
                                            "candidate_offset": 0xC, "target_offset": 8}]
    # A different VALUE at another offset is not an offset constraint.
    assert run(ORIGINAL.replace("sw t0,8(a0)", "sw zero,0xc(a0)"))["offset_constraints"] == []


def test_member_names_fire_from_measured_rows():
    rows = [{"member": "pad00", "offset": 0, "width": 8, "array": True},
            {"member": "image0", "offset": 8, "width": 4},
            {"member": "pos", "offset": 0xC, "width": 12},
            {"member": "pos.y", "offset": 0x10, "width": 4}]
    assert trace_replay.member_at("arg0", rows, 8) == "&arg0->image0"
    assert trace_replay.member_at("arg0", rows, 0x10) == "&arg0->pos.y"
    assert trace_replay.member_at("arg0", rows, 3) == "&arg0->pad00 + 0x3 bytes"
    assert trace_replay.member_at("arg0", rows, 0x40) is None
    fields = {"a0": {"parameter": "arg0", "rows": [{"member": "field8", "offset": 8, "width": 4}]}}
    report = trace_replay.replay(record(), ORIGINAL, ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)"),
                                 entry=ENTRY, size=SIZE, arities={"callee": 2}, return_registers=("v0",),
                                 symbol_map={"callee": CALLEE}, fields=fields)
    assert report["divergence"]["target"]["at"] == "a0+0x8 (&arg0->field8)"
    assert report["divergence"]["candidate"]["at"] == "a0+0xc"


def test_different_frame_size_is_not_behaviour():
    bigger = (ORIGINAL.replace("-0x18", "-0x20").replace("0x14(sp)", "0x1c(sp)")
              .replace("addiu sp,sp,0x18", "addiu sp,sp,0x20"))
    assert run(bigger)["status"] == "passed"


def test_recording_the_original_cannot_reproduce_is_unusable_not_a_verdict():
    report = run(ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)"), record(written_value=0x9999))
    assert report["status"] == "unusable"
    assert any("writes differ" in reason for reason in report["reasons"])


def test_recorded_return_value_is_checked_by_the_gate():
    report = run(rec=record(v0=0x55, extra_events=()))
    assert report["status"] == "passed"
    rec = record()
    rec["exit"]["gpr"] = gpr(v0=0x66, sp=SP, ra=RA)
    assert run(rec=rec)["status"] == "unusable"


def test_unbound_global_makes_recording_unusable():
    program = SimpleNamespace(symbols={"gSomething", "D_80123456"}, data_words={}, data_bytes={},
                              symbol_addresses={})
    with pytest.raises(trace_replay.UnusableRecording, match="gSomething") as caught:
        trace_replay.program_symbols(program, {}, set())
    # Hex-named data carries its own address and is not reported as unbound.
    assert "D_80123456" not in str(caught.value)


def test_prompt_is_evidence_labelled_and_names_the_divergence():
    failed = {**run(ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)")), "entry_ordinal": 3}
    text = trace_replay.prompt([failed, run()])
    assert "RECORDED GAME EXECUTION" in text and "1 of 2" in text
    assert "Recorded call #3" in text and "a0+0x8" in text
    item = trace_replay.feedback_item(failed)
    assert item["source"] == "recorded game execution of the original ROM"
    json.dumps(item)


def test_access_without_a_value_makes_the_recording_unusable():
    rec = record(extra_events=[["r", ENTRY + 8, A0 + 0x40, TYPES["f32"], None, None]])
    with pytest.raises(trace_replay.UnusableRecording, match="without a value"):
        trace_replay.decode(rec, ENTRY, ENTRY + SIZE)

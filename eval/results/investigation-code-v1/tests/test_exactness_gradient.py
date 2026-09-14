import hashlib
import sqlite3

from solver import exactness_gradient


REGISTER_CYCLE_DIFF = """\
--- target
+++ candidate
 move t0,zero
-addiu t5,a2,2
+addiu t3,a2,2
-move t3,zero
+move t4,zero
-addu t4,s0,v0
+addu t5,s0,v0
 jr ra
 nop
"""


SOURCE = """\
typedef signed int s32;
s32 compress(s32 srcLen) {
    s32 a3;
    s32 t0;

    a3 = srcLen;
    t0 = 0;
    if (a3 > 0) {
        a3 = 0;
        a3++;
        t0 = a3;
    }
    a3 = srcLen - t0;
    return a3;
}
"""


def test_register_cycle_and_multi_epoch_local_fire_on_motivating_shape():
    gradient = exactness_gradient.build(REGISTER_CYCLE_DIFF, SOURCE)

    assert gradient.residual_classification == \
        "register-operand-only/full-stream"
    assert any(cycle.registers == ("t3", "t4", "t5")
               for cycle in gradient.register_cycles)
    a3 = next(row for row in gradient.epoch_probe_candidates
              if row.local == "a3")
    assert [epoch.initializer for epoch in a3.epochs] == [
        "srcLen", "0", "srcLen - t0"]
    assert a3.epochs[1].mutation_lines

    rendered = gradient.render()
    assert "DETERMINISTIC EXACTNESS GRADIENT" in rendered
    assert "a0 = C parameter `srcLen`" in rendered
    assert "t3->t4" in rendered and "t4->t5" in rendered
    assert "Aligned opcode examples for register correspondences" in rendered
    assert "target `move t3,zero` | candidate `move t4,zero`" in rendered
    assert "`s32 a3` (declaration L3)" in rendered
    assert "uses 6,8" in rendered
    assert "mechanical probe candidates, not claims" in rendered
    assert "Do not equate a C local's spelling" in rendered


def test_entry_abi_map_distinguishes_physical_arguments_from_local_names():
    source = """\
void encode(u8 *src, s32 srcLen, u8 *dst) {
    s32 a2;
    a2 = srcLen;
}
"""

    assert exactness_gradient.entry_abi_parameters(source) == (
        "a0 = C parameter `src` (`u8 *src`)",
        "a1 = C parameter `srcLen` (`s32 srcLen`)",
        "a2 = C parameter `dst` (`u8 *dst`)",
    )


def test_exhausted_families_are_compacted_and_not_recommended_again():
    history = [
        {
            "label": "move statement a 0->1",
            "attempt": {"compiled": True, "score": 98.0},
            "accepted_for_next_round": False,
        },
        {
            "label": "move statement b 1->0",
            "attempt": {"compiled": True, "score": 98.1},
            "accepted_for_next_round": False,
        },
        {
            "label": "materialize assignment web a at 12",
            "attempt": {"compiled": True, "score": 98.1},
            "accepted_for_next_round": False,
        },
    ]

    gradient = exactness_gradient.build(
        REGISTER_CYCLE_DIFF, SOURCE, history=history)

    families = {row.family: row for row in gradient.experiment_families}
    assert families["statement-order"].attempted == 2
    assert families["statement-order"].best_score == 98.1
    assert families["transparent-copy/materialized-web"].attempted == 1
    suggestions = "\n".join(gradient.suggested_experiments)
    assert "permute only" not in suggestions
    assert "materialize or inline" not in suggestions


def test_movement_reports_cycle_change_not_only_scalar_fault_count():
    before = exactness_gradient.build(REGISTER_CYCLE_DIFF, SOURCE)
    after = exactness_gradient.build(
        "--- target\n+++ candidate\n move t0,zero\n jr ra\n nop", SOURCE)

    movement = exactness_gradient.movement(before, after)

    assert movement["cycle_changed"] is True
    assert movement["cycles_before"] == [("t3", "t4", "t5")]
    assert movement["cycles_after"] == []


def test_store_through_pointer_does_not_start_a_pointer_value_epoch():
    source = """\
typedef unsigned short u16;
void write(u16 *dst) {
    u16 *cursor;
    cursor = dst;
    *cursor = 7;
    cursor = cursor + 1;
    *cursor = 8;
}
"""

    candidates = exactness_gradient.epoch_probe_candidates(source)
    cursor = next(row for row in candidates if row.local == "cursor")

    assert [epoch.initializer for epoch in cursor.epochs] == [
        "dst", "cursor + 1"]


def test_split_epoch_experiment_rewrites_complete_dominated_scalar_web():
    source = """\
void scan(s32 limit) {
    s32 remaining;
    s32 best;
    remaining = limit;
    best = 0;
    for (;;) {
        remaining = 0;
        while (remaining < limit) {
            remaining++;
        }
        if (best < remaining) {
            best = remaining;
        }
        remaining = limit - best;
    }
}
"""

    rows = exactness_gradient.split_epoch_experiments(source)
    row = next(item for item in rows
               if "remaining E2" in item.label)

    assert "s32 remaining;\n    s32 remaining_epoch2;" in row.source
    assert "remaining_epoch2 = 0;" in row.source
    assert "while (remaining_epoch2 < limit)" in row.source
    assert "remaining_epoch2++;" in row.source
    assert "if (best < remaining_epoch2)" in row.source
    assert "best = remaining_epoch2;" in row.source
    assert "remaining = limit - best;" in row.source


def test_split_epoch_experiment_rejects_definition_that_does_not_dominate_uses():
    source = """\
void scan(s32 limit) {
    s32 bounded;
    bounded = limit;
    for (;;) {
        if (limit > 4) {
            bounded = 4;
        }
        use(bounded);
    }
}
"""

    rows = exactness_gradient.split_epoch_experiments(source)

    assert not any("bounded E2" in item.label for item in rows)


def test_receipt_history_survives_restart_but_stops_at_source_change():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE attempts (id INTEGER, parent_attempt_id INTEGER, "
        "source_sha256 TEXT, source_code TEXT, compiled INTEGER, score REAL, "
        "exact INTEGER)")
    conn.execute(
        "CREATE TABLE attempt_edges (parent_attempt_id INTEGER, "
        "child_attempt_id INTEGER, relation TEXT, action TEXT)")
    digest = hashlib.sha256(SOURCE.encode()).hexdigest()
    other = hashlib.sha256(b"other").hexdigest()
    conn.executemany(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?)", [
            (1, None, other, "other", 1, 90.0, 0),
            (2, 1, digest, SOURCE, 1, 98.0, 0),
            (3, 2, "variant", "variant", 1, 98.1, 0),
        ])
    conn.execute(
        "INSERT INTO attempt_edges VALUES (2,3,"
        "'deterministic-exactness-search','move statement a 0->1')")

    history = exactness_gradient.receipt_history(conn, 2, SOURCE)

    assert len(history) == 1
    assert history[0]["receipt_id"] == 3
    assert history[0]["label"] == "move statement a 0->1"
    assert history[0]["source_sha256"] == "variant"
    assert history[0]["accepted_for_next_round"] is False

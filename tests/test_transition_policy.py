import sqlite3

from solver import transition_policy


OFFSET_DIFF = "-lw v0,0(a0)\n+lw v0,4(a0)"
REGISTER_DIFF = "-lw v0,0(a0)\n+lw v1,0(a0)"
STRUCTURAL_DIFF = "-lw v0,0(a0)\n+sw v0,0(a0)"
SOURCE = """\
s32 f(s32 x) {
    s32 y;
    y = x;
    return y;
}
"""


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY,
            func_addr INTEGER NOT NULL,
            source_code TEXT NOT NULL,
            source_sha256 TEXT,
            compiled INTEGER NOT NULL,
            score REAL,
            exact INTEGER,
            diff_summary TEXT
        );
        CREATE TABLE attempt_edges (
            parent_attempt_id INTEGER NOT NULL,
            child_attempt_id INTEGER NOT NULL,
            relation TEXT NOT NULL,
            action TEXT NOT NULL,
            feedback TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0
        );
    """)
    return conn


def _transition(conn: sqlite3.Connection, *, base: int, addr: int,
                function: str, action: str, parent_diff: str = OFFSET_DIFF,
                child_diff: str = "", child_compiled: bool = True,
                child_exact: bool = True) -> None:
    conn.execute("INSERT OR IGNORE INTO functions VALUES (?,?)",
                 (addr, function))
    conn.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
        (base, addr, SOURCE, None, 1, 90.0, 0, parent_diff))
    conn.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
        (base + 1, addr, SOURCE + f"/* {base} */\n", None,
         int(child_compiled), 100.0 if child_exact else 89.0,
         int(child_exact), child_diff if child_compiled else None))
    conn.execute(
        "INSERT INTO attempt_edges VALUES (?,?,?,?,?,?)",
        (base, base + 1, "deterministic-exactness-search", action, "", 0))


def test_action_labels_are_compacted_into_transferable_families():
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "split value epoch a3 E2 L8-14") == "split-value-epoch"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "move statement t0 1->0 at 40") == "statement-order"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "swap-independent-statements-21-22") == "statement-order"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "register-counter") == "register-qualifier"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "add-assign-counter") == "value-web-shape"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "late-pointer-lifetime-p") == "pointer-lifetime"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "fuse-byte-update-lookup-idx") == "expression-fusion"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "combine-load-increment-idx") == "load-increment-web"
    assert transition_policy.action_family(
        "deterministic-exactness-search",
        "direct-byte-postincrement-idx") == "direct-byte-update"


def test_loader_reconstructs_vector_response_without_source_hashes():
    conn = _database()
    _transition(conn, base=1, addr=0x1000, function="a",
                action="split value epoch y E2 L3-4")

    rows = transition_policy.load_transitions(conn)

    assert len(rows) == 1
    assert rows[0].action_family == "split-value-epoch"
    assert rows[0].vector_improved
    assert rows[0].child_exact
    assert rows[0].fault_reduction["offset"] == 1.0


def test_diff_headers_are_not_learned_as_residual_movement():
    conn = _database()
    _transition(conn, base=1, addr=0x1000, function="a", action="rewrite",
                parent_diff="--- target\told\n+++ parent\told\n@@ -1 +1 @@\n" + OFFSET_DIFF,
                child_diff="--- target\tnew\n+++ child\tnew\n@@ -2 +2 @@\n" + OFFSET_DIFF,
                child_exact=False)
    assert not transition_policy.load_transitions(conn)[0].residual_changed
    assert transition_policy.residual_content(OFFSET_DIFF) != transition_policy.residual_content(REGISTER_DIFF)


def test_leave_one_function_out_prevents_self_teaching():
    conn = _database()
    # The queried function has a lucky exact epoch split.  The only transfer
    # evidence says that family regresses and statement order solves exactly.
    _transition(conn, base=1, addr=0x1000, function="query",
                action="split value epoch y E2 L3-4")
    _transition(conn, base=3, addr=0x2000, function="other-a",
                action="split value epoch y E2 L3-4",
                child_diff=STRUCTURAL_DIFF, child_exact=False)
    _transition(conn, base=5, addr=0x3000, function="other-b",
                action="move statement y 0->1 at 10")
    policy = transition_policy.TransitionPolicy.from_db(conn)
    state = transition_policy.residual_state(OFFSET_DIFF, SOURCE)

    epoch = policy.estimate(
        "split value epoch y E2 L3-4", state,
        relation="deterministic-exactness-search",
        exclude_function="query")
    ranked = policy.rank(
        ("split value epoch y E2 L3-4", "move statement y 0->1 at 10"),
        state, relation="deterministic-exactness-search",
        exclude_function="query")

    assert epoch.support_functions == 1
    assert epoch.exact_transitions == 0
    assert ranked[0][0].startswith("move statement")
    assert ranked[0][1].exact_transitions == 1


def test_unseen_family_beats_a_known_pure_regression_and_ties_are_stable():
    conn = _database()
    _transition(conn, base=1, addr=0x1000, function="other",
                action="split value epoch y E2 L3-4",
                child_diff=STRUCTURAL_DIFF, child_exact=False)
    policy = transition_policy.TransitionPolicy.from_db(conn)
    state = transition_policy.residual_state(OFFSET_DIFF, SOURCE)
    actions = (
        "split value epoch y E2 L3-4",
        "register-y",
        "remove-register-y",
    )

    ranked = policy.rank(
        actions, state, relation="deterministic-exactness-search")

    assert [row[0] for row in ranked[:2]] == [
        "register-y", "remove-register-y"]
    assert ranked[-1][0].startswith("split value epoch")


def test_cross_validation_replays_sibling_families_with_function_held_out():
    conn = _database()
    for addr, function, base in ((0x1000, "a", 1), (0x2000, "b", 10)):
        conn.execute("INSERT INTO functions VALUES (?,?)", (addr, function))
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
            (base, addr, SOURCE, None, 1, 90.0, 0, OFFSET_DIFF))
        # Chronological first choice is the regressive epoch family.
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
            (base + 1, addr, SOURCE + "/* epoch */", None, 1, 89.0, 0,
             STRUCTURAL_DIFF))
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
            (base + 2, addr, SOURCE + "/* order */", None, 1, 100.0, 1,
             ""))
        conn.execute(
            "INSERT INTO attempt_edges VALUES (?,?,?,?,?,?)",
            (base, base + 1, "deterministic-exactness-search",
             "split value epoch y E2 L3-4", "", 0))
        conn.execute(
            "INSERT INTO attempt_edges VALUES (?,?,?,?,?,?)",
            (base, base + 2, "deterministic-exactness-search",
             "move statement y 0->1 at 10", "", 0))

    result = transition_policy.TransitionPolicy.from_db(conn).cross_validate()

    assert result["decisive_parent_count"] == 2
    assert result["covered_decisive_parent_count"] == 2
    assert result["policy_top1_success_rate"] == 1.0
    assert result["chronological_top1_success_rate"] == 0.0
    assert result["policy_top1_exact_rate"] == 1.0

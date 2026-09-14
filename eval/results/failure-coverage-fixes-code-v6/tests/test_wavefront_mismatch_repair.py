import sqlite3

from eval import wavefront_mismatch_repair as repair


def test_stored_candidate_selection_excludes_recovery_and_prior_wf1():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        create table functions(addr integer primary key, name text);
        create table attempts(
          id integer primary key, func_addr integer, source_code text,
          score real, strategy text, model text, compiled integer, exact integer);
        insert into functions values(1, 'parent');
        insert into attempts values(1,1,'recovered',99,'history-recovery','m',1,0);
        insert into attempts values(2,1,'old wf1',98,'wavefront-mismatch-repair-raw','m',1,0);
        insert into attempts values(3,1,'eligible',90,'baseline','m',1,0);
    """)
    rows = repair._stored_candidates(conn, "parent")
    assert [row["attempt_id"] for row in rows] == [3]


def test_repair_prompt_is_local_to_observed_mismatch():
    validation = {"mismatches": [{
        "kind": "argument", "callee": "leaf", "parameter": 0,
        "expected": {"kind": "memory_load", "width": 2, "signed": True},
        "observed": {"kind": "address"},
    }]}
    prompt = repair.repair_prompt(
        "parent", "void parent(void) { leaf(ptr); }", 80.0, validation,
        "addiu $a0, $s0, 4\njal leaf\nnop")
    assert "Do not reconstruct it again" in prompt
    assert '"width": 2' in prompt
    assert "jal leaf" in prompt
    assert "void parent" in prompt


def test_best_variant_prefers_contract_valid_over_higher_nonexact_score():
    bad = repair.workspace.Attempt(True, 95.0, False, "", "", "")
    good = repair.workspace.Attempt(True, 90.0, False, "", "", "")
    rows = [
        ({"variant": "raw"}, bad, {"passed": False}),
        ({"variant": "c89"}, good, {"passed": True}),
    ]
    assert repair._best_variant(rows)[0]["variant"] == "c89"


def test_target_scan_can_include_eligible_budget_deferred_without_reserving():
    plan = {"plan": {
        "frontier": [{"function": "a", "reserved_generation_tokens": 1200}],
        "budget_deferred": [{"function": "b", "reason": "max_targets"}],
    }}
    rows = repair._targets(plan, True)
    assert [row["function"] for row in rows] == ["a", "b"]
    assert rows[1]["reserved_generation_tokens"] == 0

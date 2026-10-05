"""The redraft pilot's pure parts: prompt, equal-cost budget, extraction guards, selection, slim KB.

The generator must FIRE on its motivating case (a fenced complete C file becomes a candidate), and
each guard must fire on its own case (contamination, duplicates, no code, transport errors).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from eval import redraft_pilot as rp


def test_prompt_asks_for_a_complete_replacement_with_the_evidence() -> None:
    prompt = rp.build_prompt("glabel f\n  jr ra", "void f(void){}", 71.25, "-lw v0,0(a0)",
                             '{"faults": {}}')
    assert "COMPLETE file" in prompt and "REPLACEMENT" in prompt
    assert "glabel f" in prompt and "void f(void){}" in prompt and "-lw v0,0(a0)" in prompt
    assert "71.250" in prompt
    assert '"edits"' not in prompt, "this is not the bounded-edit patch protocol"


def test_control_budget_charges_model_time_as_compiles() -> None:
    budget = rp.control_budget(120, 4, model_seconds=60.0, compile_seconds=0.25)
    assert budget["model_equivalent_compiles"] == 240
    assert budget["budget"] == 120 + 4 + 240
    assert rp.control_budget(120, 0, 0.1, 0.25)["model_equivalent_compiles"] == 1, "rounds up"
    with pytest.raises(ValueError):
        rp.control_budget(120, 4, 60.0, 0.0)


def _guard_rejecting(marker: str):
    def guard(source: str) -> None:
        if marker in source:
            raise RuntimeError("CONTAMINATION: " + marker)
    return guard


def test_extraction_fires_and_each_guard_fires_on_its_case() -> None:
    fenced = "thinking...\n```c\n#include \"common.h\"\nvoid f(void) { int a; }\n```\n"
    responses = [
        (1, fenced, 10.0, ""),                                              # ok
        (2, fenced, 11.0, ""),                                              # duplicate of 1
        (3, "I cannot help with that.", 5.0, ""),                           # no code
        (4, "```c\nvoid f(void) { LEAKED(); }\n```", 9.0, ""),              # contaminated
        (5, "", 900.0, "TimeoutError: deadline"),                           # transport
        (6, "```c\nvoid f(void) { }\n```", 8.0, ""),                        # the incumbent itself
    ]
    out = rp.extract_redrafts(responses, incumbent="void f(void) { }",
                              guard=_guard_rejecting("LEAKED"))
    assert [r.status for r in out] == ["ok", "duplicate", "no-code", "contaminated",
                                       "transport-error", "duplicate"]
    assert out[0].source.startswith('#include "common.h"') and "int a;" in out[0].source
    assert sum(r.seconds for r in out) == pytest.approx(943.0), "every call's time is charged"


def test_extraction_keeps_resolvable_includes_and_drops_the_rest() -> None:
    """The canary's failure: llm.extract_c deleted the game/ headers the model had kept."""
    text = ("<think>```c\nvoid scratch(void) {}\n```</think>\n```c\n"
            '#include "common.h"\n#include "game/ui.h"\n#include <stdint.h>\n'
            "void f(RowActor *a) { a->x = 1; }\n```\n")
    resolvable = {"common.h", "game/ui.h"}
    source = rp.extract_source(text, lambda name: name in resolvable)
    assert '#include "game/ui.h"' in source and '#include "common.h"' in source
    assert "stdint" not in source
    assert "scratch" not in source, "the reasoning trace's fragments are not the answer"
    assert rp.extract_source("```c\nvoid f(void) {\n  int a;\n", lambda n: True).startswith("void f")
    assert rp.extract_source("```\nglabel f\n jr ra\n```", lambda n: True) == ""


def test_model_time_is_the_original_generation_time_even_on_a_cache_hit() -> None:
    assert rp.generation_seconds({"total_duration": 64_000_000_000}, wall=0.01) == 64.0
    assert rp.generation_seconds({"total_duration": 64_000_000_000, "_cache_hit": True},
                                 wall=0.01) == 64.0
    assert rp.generation_seconds({}, wall=12.5) == 12.5
    with pytest.raises(ValueError):
        rp.generation_seconds({"_cache_hit": True}, wall=0.01)


def test_model_time_is_charged_by_tokens_so_sharing_the_gpu_is_not_billed() -> None:
    # A concurrent call's wall time stretches with its neighbours' load; its tokens do not.
    alone = rp.generation_seconds({"eval_count": 1273, "total_duration": 12_744_000_000}, wall=13)
    shared = rp.generation_seconds({"eval_count": 1273, "total_duration": 30_000_000_000}, wall=30)
    assert alone == shared == pytest.approx(10 + rp.SECONDS_PER_CALL)
    assert rp.generation_seconds({"eval_count": 1273, "_cache_hit": True}, wall=0.01) == alone


def test_a_killed_run_leaves_no_rows_for_its_rerun_to_count() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table attempts (id integer primary key, run_id text)")
    conn.executemany("insert into attempts (run_id) values (?)",
                     [("redraft-pilot-f-body",), ("redraft-pilot-f-body",), ("redraft-pilot-g-body",)])
    assert rp.retire_partial(conn, "redraft-pilot-f-body") == 2
    assert conn.execute(
        "select count(*) from attempts where run_id='redraft-pilot-f-body'").fetchone()[0] == 0
    assert conn.execute("select count(*) from attempts where run_id='redraft-pilot-g-body'"
                        ).fetchone()[0] == 1


def test_two_arms_never_hold_one_function(tmp_path: Path) -> None:
    pytest.importorskip("fcntl")
    with rp.function_lock(tmp_path, "f") as first:
        assert first
        with rp.function_lock(tmp_path, "f") as second:
            assert not second      # flock is per open file description, so this is exclusive
        with rp.function_lock(tmp_path, "g") as other:
            assert other
    with rp.function_lock(tmp_path, "f") as again:
        assert again


def test_control_prefix_is_the_control_at_a_given_cost(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "kb.sqlite")
    conn.execute("create table attempts (id integer primary key, run_id text, score real, "
                 "compiled integer, exact integer)")
    conn.executemany("insert into attempts values (?,?,?,?,?)", [
        (1, "ctl", 70.0, 1, 0), (2, "ctl", 0.0, 0, 0), (3, "other", 99.0, 1, 0),
        (4, "ctl", 75.0, 1, 0), (5, "ctl", 100.0, 1, 1)])
    assert rp.control_prefix(conn, "ctl", 2) == {"compiles": 2, "best_score": 70.0,
                                                 "exact_attempt": None}
    assert rp.control_prefix(conn, "ctl", 3)["best_score"] == 75.0, "other runs are not counted"
    assert rp.control_prefix(conn, "ctl", 10)["exact_attempt"] == 5
    assert rp.control_prefix(conn, "ctl", 0)["compiles"] == 0


def test_band_boundaries() -> None:
    assert rp.band_of(30) == "<=30" and rp.band_of(31) == "31-80" and rp.band_of(80) == "31-80"
    assert rp.band_of(81) is None and rp.band_of(None) is None


def _campaign(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
    create table tus (id integer primary key, name text);
    create table functions (addr integer primary key, name text, tu_id integer, insn_count integer);
    create table attempts (id integer primary key, func_addr integer, score real, compiled integer,
        exact integer, source_code text, created_at integer);
    create table attempt_edges (parent_attempt_id integer, child_attempt_id integer);
    create table evidence (id integer primary key, fact text);
    create index ev on evidence(fact);
    """)
    conn.execute("insert into tus values (1, 'build/src/game/a.o'), (2, 'build/src/libmus/p.o')")
    fns = [(1, "f_pick", 1, 20), (2, "f_exact", 1, 20), (3, "f_sealed", 1, 20), (4, "f_lib", 2, 20),
           (5, "f_goodroot", 1, 20), (6, "f_nearmiss", 1, 20), (7, "f_big", 1, 300),
           (8, "f_mid", 1, 50), (9, "f_otherledger", 1, 20)]
    conn.executemany("insert into functions values (?,?,?,?)", fns)
    rows = [  # id, addr, score, compiled, exact
        (10, 1, 80.0, 1, 0), (11, 1, 90.0, 1, 0),          # root 80, incumbent 90 via edge
        (20, 2, 70.0, 1, 0), (21, 2, 100.0, 1, 1),
        (30, 3, 60.0, 1, 0), (40, 4, 60.0, 1, 0),
        (50, 5, 96.0, 1, 0),                                  # root >= 95
        (60, 6, 70.0, 1, 0), (61, 6, 99.5, 1, 0),             # incumbent >= 99
        (70, 7, 50.0, 1, 0), (80, 8, 75.0, 1, 0), (90, 9, 40.0, 1, 0)]
    conn.executemany("insert into attempts values (?,?,?,?,?,'src',0)", rows)
    conn.executemany("insert into attempt_edges values (?,?)", [(10, 11), (20, 21), (60, 61)])
    conn.execute("insert into evidence (fact) values ('kept')")
    conn.commit()
    return conn


def test_selection_excludes_each_case_and_picks_the_rest(tmp_path: Path) -> None:
    conn = _campaign(tmp_path / "campaign.sqlite")
    other = sqlite3.connect(tmp_path / "other.sqlite")
    other.executescript("create table functions (addr integer, name text);"
                        "create table attempts (func_addr integer, exact integer);"
                        "insert into functions values (9, 'f_otherledger');"
                        "insert into attempts values (9, 1);")
    other.commit()
    other.close()
    picked = rp.select(conn, other_exact_dbs=[tmp_path / "other.sqlite"], sealed={"f_sealed"},
                       library_patterns=("%libmus%",), per_band=5, seed=1)
    names = {row["function"] for row in picked["selected"]}
    assert names == {"f_pick", "f_mid"}
    pick = next(r for r in picked["selected"] if r["function"] == "f_pick")
    assert pick["root_best"] == 80.0, "the root is the edge-less attempt"
    assert pick["incumbent_score"] == 90.0 and pick["incumbent_attempt"] == 11
    assert picked["excluded"] == {"exact-in-a-ledger": 2, "sealed-eval-set": 1, "library-tu": 1,
                                  "root-already->=95": 1, "incumbent->=99-near-miss": 1,
                                  "outside-size-bands": 1}


def test_calibration_picks_matched_functions_by_their_best_nonexact_root(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "c.sqlite")
    conn.executescript("""
    create table tus (id integer primary key, name text);
    create table functions (addr integer primary key, name text, tu_id integer, insn_count integer);
    create table attempts (id integer primary key, func_addr integer, score real, compiled integer,
        exact integer, source_code text);
    create table attempt_edges (parent_attempt_id integer, child_attempt_id integer);
    insert into tus values (1, 'build/src/game/a.o');
    insert into functions values (1,'m_hi',1,20),(2,'m_lo',1,20),(3,'u_hi',1,20),(4,'m_child',1,20);
    -- m_hi: matched, root 97 (and a worse root 96)
    insert into attempts values (10,1,96.0,1,0,'s'),(11,1,97.0,1,0,'s'),(12,1,100.0,1,1,'s');
    insert into attempt_edges values (11,12);
    -- m_lo: matched but its root is below 95
    insert into attempts values (20,2,80.0,1,0,'s'),(21,2,100.0,1,1,'s');
    -- u_hi: root 98 but never matched
    insert into attempts values (30,3,98.0,1,0,'s');
    -- m_child: matched, its only >=95 non-exact attempt is a CHILD, not a root
    insert into attempts values (40,4,70.0,1,0,'s'),(41,4,96.0,1,0,'s'),(42,4,100.0,1,1,'s');
    insert into attempt_edges values (40,41),(41,42);
    """)
    rows = rp.select_calibration(conn, sealed=set(), library_patterns=(), per_band=5, seed=1)
    assert [(r["function"], r["root_attempt"], r["root_score"]) for r in rows] == \
        [("m_hi", 11, 97.0)]


def test_slim_kb_keeps_schema_and_static_rows_but_no_attempts(tmp_path: Path) -> None:
    source = tmp_path / "campaign.sqlite"
    _campaign(source).close()
    dest = tmp_path / "slim.sqlite"
    rp.slim_kb(source, dest)
    with sqlite3.connect(dest) as slim:
        assert slim.execute("select count(*) from functions").fetchone()[0] == 9
        assert slim.execute("select count(*) from evidence").fetchone()[0] == 1
        assert slim.execute("select count(*) from attempts").fetchone()[0] == 0
        assert slim.execute("select count(*) from sqlite_master where name='ev'").fetchone()[0] == 1
    with pytest.raises(FileExistsError):
        rp.slim_kb(source, dest)


def test_arm_outcome_needs_independent_verification_for_a_match(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "kb.sqlite")
    conn.execute("create table attempts (id integer primary key, run_id text, score real, "
                 "compiled integer, exact integer)")
    conn.executemany("insert into attempts values (?,?,?,?,?)", [
        (1, "arm", 90.0, 1, 0), (2, "arm", 100.0, 1, 1), (3, "arm", 0.0, 0, 0),
        (4, "other", 100.0, 1, 1)])
    verified = rp.arm_outcome(conn, "arm", verify=lambda aid: aid == 2)
    assert verified.matched and verified.best_attempt_id == 2 and verified.compiles == 3
    frontend_rejected = rp.arm_outcome(conn, "arm", verify=lambda aid: False)
    assert not frontend_rejected.matched and frontend_rejected.best_score == 100.0
    assert rp.arm_outcome(conn, "empty", verify=lambda aid: True).compiles == 0


# --- the arm end to end, with a stubbed compiler and model --------------------------------------

TARGET_LISTING = ["addiu    sp,sp,-0x20", "jal    g", "lw    t0,0(a0)", "jr    ra"]


def _stub_arm(monkeypatch, tmp_path):
    """A world where the model's one alternative REPAIRS the call sequence but LOWERS the score:
    the plain tree must reject it (score fell), the gated tree must accept it (object truth rose)."""
    import difflib
    from eval import campaign_workers
    from solver import llm, residual_sites, workspace

    kb = sqlite3.connect(tmp_path / "pilot.sqlite")
    kb.execute("create table attempts (id integer primary key, run_id text, iteration integer, score real, "
               "compiled integer, exact integer, source_code text)")
    campaign = sqlite3.connect(tmp_path / "campaign.sqlite")
    campaign.execute("create table attempts (id integer primary key, source_code text, score real)")
    campaign.execute("insert into attempts values (7, ?, 50.0)", ("void f(void) {\n    a = 1;\n}\n",))
    root = tmp_path / "iso"
    (root / "nonmatchings" / "f").mkdir(parents=True)

    def score(ws, iso, tag, code, conn=None, func="", **kw):
        fixed = "a = 3" in code
        listing = TARGET_LISTING if fixed else [l for l in TARGET_LISTING if "jal" not in l]
        if fixed:
            listing = [l.replace("t0", "t1") for l in listing]     # only registers remain
        diff = "\n".join(difflib.unified_diff(TARGET_LISTING, listing, "target", tag,
                                              lineterm="", n=1))
        value = 40.0 if fixed else 50.0
        rid = conn.execute("insert into attempts (run_id, score, compiled, exact, source_code) "
                           "values (?,?,1,0,?)", (kw.get("run_id", ""), value, code)).lastrowid
        (ws / "target_object_dump_normalized.s").write_text("\n".join(TARGET_LISTING))
        (ws / f"{tag}_object_dump_normalized.s").write_text("\n".join(listing))
        return workspace.Attempt(True, value, False, diff, "", "", receipt_id=rid)

    monkeypatch.setattr(campaign_workers, "isolate", lambda repo, d, n: root)
    monkeypatch.setattr(workspace, "score", score)
    monkeypatch.setattr(workspace, "target_asm", lambda ws, n: "glabel f")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a, **k: None)
    monkeypatch.setattr(residual_sites, "render", lambda *a, **k: "")
    monkeypatch.setattr(llm, "host", lambda: "stub")
    answer = json.dumps({"branch_points": [{"slot": "L2", "why": "call placement",
                                            "alternatives": ["    a = 3;"]}]})
    prompts = []

    def generate(host, model, prompt, **kw):
        prompts.append(prompt)
        return answer, {"total_duration": 2_000_000_000}
    monkeypatch.setattr(llm, "generate", generate)
    monkeypatch.setattr(rp, "polish", lambda *a, **k: ["stub polish"])
    row = {"function": "f", "incumbent_attempt": 7, "_pilot_compile_seconds": 0.25}
    kwargs = dict(repo=tmp_path, native=tmp_path, conn=kb, campaign=campaign, model="stub",
                  samples=1, polish_budget=0, think="low", temperature=0.0, depth=2)
    return row, kwargs, prompts


def test_plain_tree_runs_end_to_end_and_rejects_a_score_drop(monkeypatch, tmp_path) -> None:
    row, kwargs, _ = _stub_arm(monkeypatch, tmp_path)
    report = rp.run_branch_arm(row, **kwargs)
    assert report["status"] == "done", report.get("error")
    assert report["levels"][0]["improving_children"] == 0, "score fell: plain tree must reject"
    # Arms report the raw best of what THEY compiled (the rejected child, 40); the incumbent is
    # reported separately and applied as a floor to every arm alike at analysis time.
    assert report["branch_arm"]["best_score"] == 40.0 and report["incumbent_score_here"] == 50.0


def test_equivalence_arm_runs_end_to_end(monkeypatch, tmp_path) -> None:
    """The `index` shadowing bug: every branch-arm run raised before its first prompt."""
    row, kwargs, prompts = _stub_arm(monkeypatch, tmp_path)
    rules = tmp_path / "eq.json"
    rules.write_text(json.dumps({"schema_version": 1, "rules": []}))
    report = rp.run_branch_arm(row, equivalences=rules, **kwargs)
    assert report["status"] == "done" and report["branch_version"].endswith("-eq")
    assert prompts, "the model was never asked"


def test_gated_tree_accepts_an_object_truth_repair_the_score_disliked(monkeypatch, tmp_path) -> None:
    row, kwargs, prompts = _stub_arm(monkeypatch, tmp_path)
    report = rp.run_branch_arm(row, gated=True, **kwargs)
    assert report["status"] == "done", report.get("error")
    assert report["branch_version"].endswith("-gated")
    assert report["levels"][0]["kinds"].get("truth-up") == 1
    assert report["truth"]["incumbent_level"] == "calls"
    assert report["truth"]["best_level"] == "registers"
    assert "FIRST UNMATCHED LEVEL" in prompts[0] and "CALLS" in prompts[0]


def test_gated_tree_measures_moved_calls_from_complete_listings(monkeypatch, tmp_path) -> None:
    """Hunk-local call edits cannot reject a child that fixes the actual call sequence."""
    import difflib
    from solver import workspace

    row, kwargs, _ = _stub_arm(monkeypatch, tmp_path)
    middle = [f"ori t0,t0,{n}" for n in range(20)]
    target = ["jal foo", "nop"] + middle + ["jr ra", "nop"]
    moved = ["nop"] + middle + ["jal foo", "jr ra", "nop"]

    def score(ws, iso, tag, code, conn=None, func="", **kw):
        fixed = "a = 3" in code
        listing = moved if fixed else [s.replace("jal foo", "jal wrong") for s in target]
        value = 40.0 if fixed else 50.0
        diff = "\n".join(difflib.unified_diff(target, listing, "target", tag, lineterm="", n=3))
        rid = conn.execute("insert into attempts (run_id, score, compiled, exact, source_code) "
                           "values (?,?,1,0,?)", (kw.get("run_id", ""), value, code)).lastrowid
        (ws / "target_object_dump_normalized.s").write_text("\n".join(target))
        (ws / f"{tag}_object_dump_normalized.s").write_text("\n".join(listing))
        return workspace.Attempt(True, value, False, diff, "", "", receipt_id=rid)

    monkeypatch.setattr(workspace, "score", score)
    report = rp.run_branch_arm(row, gated=True, **kwargs)
    assert report["levels"][0]["kinds"].get("truth-up") == 1
    assert report["truth"]["best"] == [0, 0, 0, 2, 0, 0]
    assert report["truth"]["best_level"] == "expressions"


def test_mined_integer_cast_rule_cannot_skip_a_float_candidate(monkeypatch, tmp_path) -> None:
    """An integer no-op pattern also matches a meaningful float-to-int conversion."""
    from patterns import equivalences as eq
    from solver import llm

    row, kwargs, _ = _stub_arm(monkeypatch, tmp_path)
    rows = [(i, f"f{i}", f"tu{i}", f"int f{i}(int x) {{ return (int)x; }}",
             f"int f{i}(int x) {{ return x; }}", True) for i in range(5)]
    rules = eq.rules(eq.mine(rows), min_noop=5, min_functions=5)
    parent = "float f(float x) {\n    return (int)x;\n}\n"
    child = "float f(float x) {\n    return x;\n}\n"
    assert eq.Index(rules).predicts_noop(parent, child), "the historical rule matches this case"
    path = tmp_path / "cast-rules.json"
    path.write_text(json.dumps({"schema_version": 1, "rules": rules}))
    kwargs["campaign"].execute("update attempts set source_code=? where id=7", (parent,))
    answer = json.dumps({"branch_points": [{"slot": "L2", "why": "remove narrowing cast",
                                            "alternatives": ["    return x;"]}]})
    monkeypatch.setattr(llm, "generate", lambda *a, **k: (answer, {"total_duration": 1e9}))
    rp.run_branch_arm(row, equivalences=path, **kwargs)
    assert kwargs["conn"].execute("select count(*) from attempts where source_code=?",
                                   (child,)).fetchone()[0] >= 1


def test_rescue_arm_end_to_end_rescues_a_dialect_failure(monkeypatch, tmp_path) -> None:
    from eval import campaign_workers
    from solver import workspace
    kb = sqlite3.connect(tmp_path / "pilot.sqlite")
    kb.execute("create table attempts (id integer primary key, run_id text, strategy text, "
               "score real, compiled integer, exact integer, source_code text, "
               "compiler_stderr text, wall_ms integer)")
    stamp = "redraft-pilot-f"
    kb.executemany("insert into attempts (run_id, strategy, score, compiled, exact, source_code, "
                   "compiler_stderr, wall_ms) values (?,?,?,?,?,?,?,?)", [
                       (f"{stamp}-model", "model-redraft", 0.0, 0, 0,
                        "void f(void) {\n    int16_t x = 1;\n}\n", "Syntax Error", 30000),
                       (f"{stamp}-model", "model-redraft", 55.0, 1, 0,
                        "void f(void) {\n    s32 y;\n}\n", "", 20000)])
    root = tmp_path / "iso"
    (root / "nonmatchings" / "f").mkdir(parents=True)

    def score(ws, iso, tag, code, conn=None, func="", **kw):
        ok = "int16_t" not in code
        rid = conn.execute("insert into attempts (run_id, strategy, score, compiled, exact, "
                           "source_code) values (?,?,?,?,0,?)",
                           (kw.get("run_id", ""), kw.get("strategy", ""), 61.0 if ok else 0.0,
                            int(ok), code)).lastrowid
        return workspace.Attempt(ok, 61.0 if ok else 0.0, False, "@@\n-a\n+b", "" if ok else
                                 "Syntax Error", "", receipt_id=rid)
    monkeypatch.setattr(campaign_workers, "isolate", lambda repo, d, n: root)
    monkeypatch.setattr(workspace, "score", score)
    monkeypatch.setattr(workspace, "target_asm", lambda ws, n: "glabel f")
    polished = []
    monkeypatch.setattr(rp, "polish", lambda *a, **k: polished.append(a[4]) or ["stub"])
    report = rp.run_rescue_arm({"function": "f", "_pilot_compile_seconds": 0.25}, repo=tmp_path,
                               native=tmp_path, conn=kb, campaign=None, model="stub", samples=1,
                               polish_budget=0, think="low", temperature=0.0)
    assert report["status"] == "done" and report["rescued"] == 1
    assert report["rescues"][0]["rescued_by"] == "c89"
    assert report["best_root"] == {"score": 61.0, "origin": "c89"}, "the rescued redraft won"
    assert "s16 x" in polished[0]
    assert report["equal_cost"]["model_seconds"] == 50.0, "original generation time is charged"


def test_body_arm_end_to_end_compiles_only_the_models_function(monkeypatch, tmp_path) -> None:
    """The model's typedefs and helpers are discarded; its body is assembled into the incumbent's
    context; a failure naming an undeclared identifier is attributed to context."""
    from eval import campaign_workers
    from solver import llm, workspace
    kb = sqlite3.connect(tmp_path / "pilot.sqlite")
    kb.execute("create table attempts (id integer primary key, run_id text, score real, "
               "compiled integer, exact integer, source_code text, diff_summary text)")
    campaign = sqlite3.connect(tmp_path / "campaign.sqlite")
    campaign.execute("create table attempts (id integer primary key, source_code text, score real)")
    incumbent = '#include "common.h"\nextern s32 gCount;\n\nvoid f(void) {\n    gCount = 1;\n}\n'
    campaign.execute("insert into attempts values (7, ?, 50.0)", (incumbent,))
    root = tmp_path / "iso"
    (root / "nonmatchings" / "f").mkdir(parents=True)
    compiled_sources = []

    def score(ws, iso, tag, code, conn=None, func="", **kw):
        compiled_sources.append(code)
        bad = "gMissing" in code
        value = 0.0 if bad else (60.0 if "gCount = 2" in code else 50.0)
        rid = conn.execute("insert into attempts (run_id, score, compiled, exact, source_code, "
                           "diff_summary) values (?,?,?,0,?,?)",
                           (kw.get("run_id", ""), value, int(not bad), code, f"d{value}")).lastrowid
        frontend = {"diagnostics": "candidate.c:5:5: error: use of undeclared identifier 'gMissing'"}
        return workspace.Attempt(not bad, value, False, "@@\n-a\n+b",
                                 "cfe: Error: 'gMissing' undefined" if bad else "", "",
                                 receipt_id=rid, frontend=frontend if bad else None)
    answers = iter([
        "```c\ntypedef int Junk;\nstatic void helper(void) {}\nvoid f(void) {\n    gCount = 2;\n}\n```",
        "```c\nvoid f(void) {\n    gMissing = 2;\n}\n```"])
    monkeypatch.setattr(campaign_workers, "isolate", lambda repo, d, n: root)
    monkeypatch.setattr(workspace, "score", score)
    monkeypatch.setattr(workspace, "target_asm", lambda ws, n: "glabel f")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a, **k: None)
    monkeypatch.setattr(llm, "host", lambda: "stub")
    monkeypatch.setattr(llm, "generate", lambda *a, **k: (next(answers), {"total_duration": 1e9}))
    monkeypatch.setattr(rp, "polish", lambda *a, **k: ["stub"])
    report = rp.run_body_arm({"function": "f", "incumbent_attempt": 7, "_pilot_compile_seconds": 0.25},
                             repo=tmp_path, native=tmp_path, conn=kb, campaign=campaign, model="stub",
                             samples=2, polish_budget=0, think="low", temperature=0.0)
    assert report["status"] == "done" and report["context_valid"] is True
    good, bad = report["bodies"]
    assert good["compiled_raw"] and good["score"] == 60.0
    assert bad["failure"] == "needs-context" and bad["unknown_names"] == ["gMissing"]
    assembled = [s for s in compiled_sources if "gCount = 2" in s][0]
    assert "Junk" not in assembled and "helper" not in assembled, "only the model's function is kept"
    assert assembled.startswith('#include "common.h"\nextern s32 gCount;')
    assert report["best_root"] == 60.0
    assert report["distinct_objects"] == 1


def test_body_arm_repair_round_uses_the_undeclared_name_check(monkeypatch, tmp_path) -> None:
    from eval import campaign_workers
    from solver import llm, workspace
    kb = sqlite3.connect(tmp_path / "pilot.sqlite")
    kb.execute("create table attempts (id integer primary key, run_id text, score real, "
               "compiled integer, exact integer, source_code text, diff_summary text)")
    campaign = sqlite3.connect(tmp_path / "campaign.sqlite")
    campaign.execute("create table attempts (id integer primary key, source_code text, score real)")
    campaign.execute("insert into attempts values (7, ?, 55.0)",
                     ('extern s32 gCount;\n\nvoid f(void) {\n    gCount = 1;\n}\n',))
    root = tmp_path / "iso"
    (root / "nonmatchings" / "f").mkdir(parents=True)

    def score(ws, iso, tag, code, conn=None, func="", **kw):
        bad = "gCont" in code
        value = 0.0 if bad else 55.0
        rid = conn.execute("insert into attempts (run_id, score, compiled, exact, source_code, "
                           "diff_summary) values (?,?,?,0,?,?)",
                           (kw.get("run_id", ""), value, int(not bad), code, f"d{value}")).lastrowid
        return workspace.Attempt(not bad, value, False, f"d{value}", "Error: 'gCont' undefined" if bad
                                 else "", "", receipt_id=rid)
    prompts = []
    answers = iter(["```c\nvoid f(void) {\n    gCont = 2;\n}\n```",
                    "```c\nvoid f(void) {\n    gCount = 2;\n}\n```"])

    def generate(host, model, prompt, **kw):
        prompts.append(prompt)
        return next(answers), {"total_duration": 1e9}
    monkeypatch.setattr(campaign_workers, "isolate", lambda repo, d, n: root)
    monkeypatch.setattr(workspace, "score", score)
    monkeypatch.setattr(workspace, "target_asm", lambda ws, n: "glabel f")
    monkeypatch.setattr(workspace, "assert_uncontaminated", lambda *a, **k: None)
    monkeypatch.setattr(llm, "host", lambda: "stub")
    monkeypatch.setattr(llm, "generate", generate)
    monkeypatch.setattr(rp, "polish", lambda *a, **k: ["stub"])
    report = rp.run_body_arm({"function": "f", "incumbent_attempt": 7, "_pilot_compile_seconds": 0.25},
                             repo=tmp_path, native=tmp_path, conn=kb, campaign=campaign, model="stub",
                             samples=1, polish_budget=0, think="low", temperature=0.0, repairs=1)
    entry = report["bodies"][0]
    assert entry["undeclared"] == ["gCont"] and entry["compiled_after_repair"] == 1
    assert "gCont: not declared anywhere; did you mean gCount?" in prompts[1]
    assert report["best_root"] == 55.0 and report["repairs"] == 1


def test_v5_tree_steers_prefilters_noops_and_enumerates_sensitive_families(monkeypatch, tmp_path) -> None:
    """--steer puts the measured table in the prompt; --stage-filter skips an alternative whose
    optimizer output equals the node's WITHOUT compiling it; --hybrid adds deterministic
    children from the sensitive families."""
    from solver import ido_stages
    from solver import regalloc_mutations as rm
    row, kwargs, prompts = _stub_arm(monkeypatch, tmp_path)
    # the model proposes a respelling that IDO flattens, plus the real fix
    answer = json.dumps({"branch_points": [{"slot": "L2", "why": "spelling",
                                            "alternatives": ["    a = (1);", "    a = 3;"]}]})
    from solver import llm
    monkeypatch.setattr(llm, "generate", lambda *a, **k: (prompts.append(a[2]) or answer,
                                                          {"total_duration": 1e9}))
    compiled = []
    from solver import workspace
    real_score = workspace.score

    def counting_score(*a, **k):
        compiled.append(a[3])
        return real_score(*a, **k)
    monkeypatch.setattr(workspace, "score", counting_score)
    # stub optimizer key: parentheses and whitespace do not survive the front end
    monkeypatch.setattr(ido_stages, "optimizer_key",
                        lambda repo, ws, fn, src: "".join(src.split()).replace("(1)", "1"))
    monkeypatch.setattr(rm, "statement_moves", lambda src, fn, limit=10: iter(
        [("stmt_move:0->1", "stmt_move", src.replace("a = 1;", "a = 3; /* moved */"))]))
    for gen in ("commutative_swaps", "declaration_swaps", "local_types"):
        monkeypatch.setattr(rm, gen, lambda src, fn, limit=10: iter([]))
    report = rp.run_branch_arm(row, gated=True, steer=True, stage_filter=True, hybrid=True, **kwargs)
    assert report["status"] == "done", report.get("error")
    assert report["branch_version"].endswith("-gated-steer-filter-hybrid")
    kinds = report["levels"][0]["kinds"]
    assert kinds.get("noop-prefiltered") == 1, kinds
    assert "    a = (1);" not in "".join(compiled), "the flattened respelling was never compiled"
    assert any(k.startswith("det:") for k in kinds), "deterministic enumeration ran"
    assert "WHAT IDO IGNORES AND WHAT IT RESPONDS TO" in prompts[0]
    assert report["equal_cost"]["key_seconds"] >= 0.0
    # a proposal skipped without compiling still leaves a record of what the model proposed
    logged = kwargs["conn"].execute("select status, kind, hypothesis from model_proposals").fetchall()
    assert ("duplicate", "optimizer-key:noop", "spelling") in logged, logged

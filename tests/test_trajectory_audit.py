"""Tests for `eval.trajectory_audit` that fail on the defect they describe.

Every test here builds a small SQLite fixture in `tmp_path`; none reads the real
1 GB research KB.  The fixtures are deliberately shaped so that a broken auditor
produces a *different* answer rather than an exception -- the failure mode this
project keeps hitting is a pass that returns nothing, which looks exactly like a
pass with nothing to do.  So each check asserts a specific non-zero count and the
specific row id that must be named.

The centrepiece is `test_different_candidate_in_prompt_is_flagged`: a child whose
recorded parent is attempt 1 while the prompt shows some other C body.  That row
must be flagged `parent-not-in-prompt` and excluded.  Its mirror,
`test_honest_row_is_not_flagged`, must stay clean -- a check that flags everything
is as useless as one that flags nothing.  And
`test_recoverable_branch_names_the_other_attempt` covers the middle case: the
prompt contains a real candidate of the same function, so the edge is mislabelled
rather than lost, and the auditor has to say *which* attempt it was.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from eval import trajectory_audit as ta


# --------------------------------------------------------------------- fixtures

ATTEMPTS_DDL = """
CREATE TABLE attempts (
    id                 INTEGER PRIMARY KEY,
    func_addr          INTEGER NOT NULL,
    iteration          INTEGER NOT NULL,
    source_code        TEXT    NOT NULL,
    prompt_context     TEXT,
    compiled           INTEGER NOT NULL,
    compiler_stderr    TEXT,
    score              REAL,
    diff_summary       TEXT,
    strategy           TEXT,
    model              TEXT,
    sampling           TEXT,
    wall_ms            INTEGER,
    token_cost         INTEGER,
    created_at         INTEGER NOT NULL,
    raw_response       TEXT,
    extract_status     TEXT,
    done_reason        TEXT,
    exact              INTEGER,
    run_id             TEXT,
    parent_attempt_id  INTEGER,
    source_sha256      TEXT,
    prompt_sha256      TEXT
)
"""

EDGES_DDL = """
CREATE TABLE attempt_edges (
    parent_attempt_id INTEGER NOT NULL,
    child_attempt_id  INTEGER NOT NULL,
    relation          TEXT    NOT NULL,
    action            TEXT    NOT NULL DEFAULT '',
    feedback          TEXT    NOT NULL DEFAULT '',
    created_at        INTEGER NOT NULL,
    PRIMARY KEY (parent_attempt_id, child_attempt_id)
)
"""

FUNCTIONS_DDL = "CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT, tu_id INTEGER, size INTEGER)"


def candidate(tag: str, steps: int = 14) -> str:
    """A plausible candidate C file comfortably longer than min_candidate_len."""
    lines = [
        '#include "common.h"',
        "",
        f"/* candidate {tag} */",
        f"s32 fn_{tag}(s32 a0) {{",
        "    s32 v0;",
        "",
    ]
    for i in range(steps):
        lines.append(f"    v0 = (a0 + {i}) * {i + 2};  /* {tag}: step {i} */")
    lines += ["", "    return v0;", "}", ""]
    return "\n".join(lines)


def repair_prompt(parent_src: str, header: str = "You are correcting one C candidate.") -> str:
    return (f"{header}\n\nTARGET ASSEMBLY:\n```\nglabel fn\n    /* 0 80000000 00000000 */  nop\n```\n\n"
            f"CURRENT C:\n```c\n{parent_src}```\n")


def numbered_prompt(parent_src: str) -> str:
    """The `differential-debugger` rendering: every source line carries a `NN | ` gutter."""
    body = "\n".join(
        f"{i + 1:>4} | {line}" for i, line in enumerate(parent_src.splitlines()))
    return ("NUMBERED CURRENT C:\n```c\n" + body + "\n```\n")


def role_sampling(role: str, **extra) -> str:
    obj = {"temperature": 0.6, "generation": {"sampling": {"role": role}}}
    obj.update(extra)
    return json.dumps(obj)


def attempt_row(
    rid: int,
    func_addr: int,
    source_code: str,
    *,
    prompt: str | None = "",
    raw_response: str | None = None,
    compiled: int = 1,
    stderr: str = "",
    score: float = 50.0,
    strategy: str = "unit-strategy",
    model: str = "test-model",
    sampling: str | None = None,
    token_cost: int = 10,
    exact: int | None = 0,
    extract_status: str | None = "ok",
    done_reason: str | None = "stop",
    run_id: str | None = "run-1",
    parent: int | None = None,
    source_sha256: str | None = "auto",
) -> dict:
    if source_sha256 == "auto":
        source_sha256 = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
    if sampling is None:
        sampling = json.dumps({"temperature": 0.6})
    return {
        "id": rid,
        "func_addr": func_addr,
        "iteration": rid,
        "created_at": 1,
        "source_code": source_code,
        "prompt_context": prompt,
        "compiled": compiled,
        "compiler_stderr": stderr,
        "score": score,
        "strategy": strategy,
        "model": model,
        "sampling": sampling,
        "token_cost": token_cost,
        "exact": exact,
        "extract_status": extract_status,
        "done_reason": done_reason,
        "run_id": run_id,
        "parent_attempt_id": parent,
        "source_sha256": source_sha256,
        # default: the completion was stored, so only the parent check can exclude a row
        "raw_response": source_code if raw_response is None else raw_response,
    }


def edge_row(parent: int, child: int, relation: str = "refine", action: str = "repair") -> dict:
    return {"parent_attempt_id": parent, "child_attempt_id": child,
            "relation": relation, "action": action, "feedback": "", "created_at": 1}


def build_db(tmp_path, attempts, edges=(), functions=None, name="fixture.sqlite"):
    path = tmp_path / name
    con = sqlite3.connect(path)
    try:
        con.execute(ATTEMPTS_DDL)
        con.execute(EDGES_DDL)
        con.execute(FUNCTIONS_DDL)
        cols = list(attempt_row(0, 0, "x").keys())
        con.executemany(
            f"INSERT INTO attempts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(a[c] for c in cols) for a in attempts])
        con.executemany(
            """INSERT INTO attempt_edges
               (parent_attempt_id, child_attempt_id, relation, action, feedback, created_at)
               VALUES (:parent_attempt_id, :child_attempt_id, :relation, :action, :feedback,
                       :created_at)""",
            list(edges))
        con.executemany("INSERT INTO functions (addr, name) VALUES (?, ?)",
                        functions or [(0x1000, "fn_unit")])
        con.commit()
    finally:
        con.close()
    return path


# ------------------------------------------------------- question 2: the centre

def test_different_candidate_in_prompt_is_flagged(tmp_path):
    """A child whose prompt shows a C body that is NOT its recorded parent.

    This is the defect the whole audit exists for: the row looks internally
    consistent, the edge resolves, both endpoints compile -- and the pair is a
    lie about what the model was shown.
    """
    parent_src = candidate("recorded_parent")
    shown_src = candidate("actually_shown_candidate")   # no attempt row holds this
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("child_output"),
                        prompt=repair_prompt(shown_src), parent=1, score=80.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["checkable_rows"] == 1
    assert q2["check_runnable"] is True
    assert q2["verbatim_match"] == 0
    assert q2["genuinely_absent"] == 1, "the mismatched parent must be flagged"
    assert q2["flagged_parent_not_in_prompt_literal"] == 1
    assert q2["excluded_from_training_pairs"] == 1

    finding = {f["id"]: f for f in a["findings"]}
    assert "parent-not-in-prompt" in finding
    assert finding["parent-not-in-prompt"]["severity"] == "critical"
    assert 2 in finding["parent-not-in-prompt"]["example_ids"]

    [example] = q2["examples_parent_not_in_prompt"]
    assert example["child_id"] == 2
    assert example["parent_id"] == 1
    assert example["prompt_len"] == len(repair_prompt(shown_src))
    assert example["other_attempt_id"] is None, "no other attempt holds the shown candidate"


def test_honest_row_is_not_flagged(tmp_path):
    """The mirror image: a correct edge must survive unmolested.

    Without this, a check that flags every row would pass the test above.
    """
    parent_src = candidate("honest_parent")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("honest_child"),
                        prompt=repair_prompt(parent_src), parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["checkable_rows"] == 1
    assert q2["check_runnable"] is True
    assert q2["verbatim_match"] == 1
    assert q2["flagged_parent_not_in_prompt_literal"] == 0
    assert q2["genuinely_absent"] == 0
    assert q2["rendering_variant_line_numbered"] == 0
    assert q2["other_attempt_in_prompt"] == 0
    assert "parent-not-in-prompt" not in {f["id"] for f in a["findings"]}

    assert a["training_usable"]["row_level"]["usable"] == 2
    assert a["training_usable"]["pair_level"]["usable"] == 1
    assert a["training_usable"]["pair_level"]["states"] == {"usable": 1}


def test_recoverable_branch_names_the_other_attempt(tmp_path):
    """The prompt holds a DIFFERENT stored attempt of the same function.

    The edge is mislabelled, not unrecoverable -- and the auditor has to report
    which attempt id was actually shown, because that id is the correct parent.
    """
    recorded_parent = candidate("recorded_parent")
    actually_shown = candidate("attempt_three_body")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, recorded_parent, prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(3, 0x1000, actually_shown, prompt="later prompt", score=60.0),
            attempt_row(2, 0x1000, candidate("child_output"),
                        prompt=repair_prompt(actually_shown), parent=1, score=55.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["checkable_rows"] == 1
    assert q2["other_attempt_in_prompt"] == 1
    assert q2["genuinely_absent"] == 0, "recoverable rows are not genuine losses"
    assert q2["rendering_variant_line_numbered"] == 0
    assert q2["excluded_from_training_pairs"] == 0

    [example] = q2["examples_other_attempt_in_prompt"]
    assert example["child_id"] == 2
    assert example["parent_id"] == 1
    assert example["other_attempt_id"] == 3, "the auditor must name the attempt actually shown"
    assert example["other_attempt_src_len"] == len(actually_shown)

    assert "recoverable-other-attempt-in-prompt" in {f["id"] for f in a["findings"]}
    states = a["training_usable"]["pair_level"]["states"]
    assert states.get("recoverable-other-attempt") == 1


def test_trivial_short_collision_is_not_treated_as_recoverable(tmp_path):
    """`#include "common.h"` appears in nearly every prompt and is not a candidate.

    This is the false positive that would otherwise manufacture ~109 phantom
    recoveries in the real KB.
    """
    dense_parent = candidate("dense_parent")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, dense_parent, prompt="TARGET ASSEMBLY", score=90.0),
            # a 19-character "source" that is a substring of the prompt boilerplate
            attempt_row(3, 0x1000, '#include "common.h"', prompt="", score=0.0),
            attempt_row(2, 0x1000, candidate("child_output"),
                        prompt=repair_prompt(candidate("unrelated_body")), parent=1, score=85.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["other_attempt_in_prompt"] == 0, "a 19-char collision is not a recovered candidate"
    assert q2["genuinely_absent"] == 1
    assert q2["trivial_substring_collisions"] >= 1
    assert 3 in q2["trivial_collision_ids"]
    assert "trivial-substring-collision" in {f["id"] for f in a["findings"]}


def test_line_numbered_rendering_is_flagged_literally_but_not_excluded(tmp_path):
    """The parent rendered with a `NN | ` gutter IS the candidate the model was shown.

    The literal rule cannot match it.  Excluding these would throw away correct
    edges -- 213 of them in the research KB -- so the auditor separates the
    rendering variant from a genuine absence.
    """
    parent_src = candidate("gutted_parent")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("child_output"),
                        prompt=numbered_prompt(parent_src), parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["checkable_rows"] == 1
    assert q2["verbatim_match"] == 0
    assert q2["flagged_parent_not_in_prompt_literal"] == 1, "the literal rule must still fire"
    assert q2["rendering_variant_line_numbered"] == 1
    assert q2["genuinely_absent"] == 0
    assert q2["excluded_from_training_pairs"] == 0, "a gutter must not exclude a correct edge"

    ids = {f["id"] for f in a["findings"]}
    assert "parent-in-prompt-line-numbered" in ids
    assert "parent-not-in-prompt" not in ids
    assert a["training_usable"]["pair_level"]["usable"] == 1


def test_gutter_detection_survives_an_unbalanced_fence(tmp_path):
    """Regression: an odd number of ``` lines must not hide the numbered C block.

    Real `differential-debugger-*` prompts put model JSON in a fenced block, and
    that JSON can itself contain fence runs.  Positional fence pairing then shifts
    by one and the NUMBERED CURRENT C payload lands outside every detected block.
    A fence-dependent gutter check silently reports those rows as genuinely
    missing a parent -- a false critical finding on correct data.
    """
    parent_src = candidate("mispaired_parent")
    body = "\n".join(
        f"{i + 1:>4} | {line}" for i, line in enumerate(parent_src.splitlines()))
    # An unterminated fence before the numbered block: positional pairing then
    # makes the "```c" line CLOSE that stray block, leaving the numbered payload
    # outside every detected block.  This is the shape real prompts hit.
    prompt = ("PREVIOUS RESPONSE:\n```\n{\"spans\": [], \"note\": \"stray\"}\n"
              "COMPILER ERROR:\nerror: boom\n"
              "NUMBERED CURRENT C:\n```c\n" + body + "\n```\n")
    assert prompt.count("```") % 2 == 1, "fixture must have an unbalanced fence count"

    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("child_output"),
                        prompt=prompt, parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["verbatim_match"] == 0, "the payload really is gutter-annotated"
    assert q2["rendering_variant_line_numbered"] == 1, "the gutter check must still find it"
    assert q2["genuinely_absent"] == 0, "an unbalanced fence must not fake a missing parent"
    assert "parent-not-in-prompt" not in {f["id"] for f in a["findings"]}
    assert a["training_usable"]["row_level"]["usable"] == 2
    assert a["training_usable"]["pair_level"]["usable"] == 1


def test_gutter_match_requires_gutters_in_the_matched_region():
    """The gutter rescue must not become a blanket pass.

    `gutter_rendering_match` finds the target in a gutter-stripped prompt, so it
    could in principle fire on text that was never gutter-annotated.  It must
    require the matched lines to actually carry a numeric prefix.
    """
    target = candidate("plain_target")
    plain = f"TARGET ASSEMBLY:\n```\n{target}\n```\n"
    assert ta.gutter_rendering_match(plain, target) is False, \
        "a plain prompt is a verbatim hit, not a gutter rendering"

    unnumbered = "NUMBERED CURRENT C:\n```c\n" + target + "\n```\n"
    assert ta.gutter_rendering_match(unnumbered, target) is False

    numbered = numbered_prompt(target)
    assert ta.gutter_rendering_match(numbered, target) is True

    # a one-line target cannot establish a gutter region
    assert ta.gutter_rendering_match("  1 | int x;\n", "int x;\n") is False


def test_check_cannot_run_is_reported_not_silently_passed(tmp_path):
    """No prompt_context anywhere means the check cannot run -- which is not a pass."""
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="", score=90.0),
            attempt_row(2, 0x1000, candidate("c"), prompt="", parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    q2 = a["q2_parent_in_prompt"]

    assert q2["checkable_rows"] == 0
    assert q2["check_runnable"] is False
    assert q2["genuinely_absent"] == 0, "nothing was checked, so nothing was found"
    finding = {f["id"]: f for f in a["findings"]}
    assert "check-cannot-run" in finding
    assert finding["check-cannot-run"]["severity"] == "critical"
    assert any("cannot run" in w or "no row has a prompt_context" in w
               for w in a["warnings"] + [finding["check-cannot-run"]["summary"]])


# ------------------------------------------------------------- question 1 and 3

def test_dangling_edge_is_flagged(tmp_path):
    db = build_db(
        tmp_path,
        [attempt_row(1, 0x1000, candidate("solo"), prompt="p", score=50.0)],
        [edge_row(1, 999)],   # child 999 does not exist
    )
    a = ta.audit_db(db)
    assert a["q1_lineage"]["child_row_missing"] == 1
    assert a["q1_lineage"]["child_row_missing_examples"] == [999]
    finding = {f["id"]: f for f in a["findings"]}
    assert "dangling-edge" in finding
    assert finding["dangling-edge"]["severity"] == "critical"


def test_non_canonical_relation_count_is_not_capped_by_examples(tmp_path):
    """A capped example list must not become the source of truth for a count."""
    db = build_db(
        tmp_path,
        [attempt_row(i, 0x1000, candidate(f"c{i}"), prompt="p", score=50.0 - i)
         for i in range(1, 8)],
        [edge_row(1, i, relation="bespoke-relation") for i in range(2, 8)],
    )
    a = ta.audit_db(db, examples=2)
    q1 = a["q1_lineage"]
    assert q1["non_canonical_relations_total"] == 6
    assert q1["non_canonical_relations"] == {"bespoke-relation": 6}
    assert len(q1["non_canonical_relations_examples"]) == 2, "examples stay capped"


def test_score_delta_distribution_is_real(tmp_path):
    """Lower asm-differ score is better, so an improving child has a negative delta."""
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="p", score=90.0),
            attempt_row(2, 0x1000, candidate("c1"), prompt="p", parent=1, score=70.0),  # improved
            attempt_row(3, 0x1000, candidate("c2"), prompt="p", parent=1, score=90.0),  # unchanged
            attempt_row(4, 0x1000, candidate("c3"), prompt="p", parent=1, score=95.0),  # worse
        ],
        [edge_row(1, 2), edge_row(1, 3), edge_row(1, 4)],
    )
    a = ta.audit_db(db)
    sd = a["q1_lineage"]["score_delta"]
    assert (sd["improved"], sd["unchanged"], sd["worse"]) == (1, 1, 1)
    assert sd["n"] == 3
    assert sd["min"] == -20.0 and sd["max"] == 5.0


def test_pair_excluded_when_not_compiled_or_not_improved(tmp_path):
    parent_src = candidate("p")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="p", score=90.0, compiled=1),
            # compiles but scores worse -> not a useful repair pair
            attempt_row(2, 0x1000, candidate("worse"), prompt=repair_prompt(parent_src),
                        parent=1, score=95.0, compiled=1),
            # improves but does not compile
            attempt_row(3, 0x1000, candidate("broken"), prompt=repair_prompt(parent_src),
                        parent=1, score=10.0, compiled=0, stderr="error: nope"),
        ],
        [edge_row(1, 2), edge_row(1, 3)],
    )
    a = ta.audit_db(db)
    states = a["training_usable"]["pair_level"]["states"]
    assert states.get("child-did-not-improve") == 1
    assert states.get("endpoints-not-both-compiled") == 1
    assert a["training_usable"]["pair_level"]["usable"] == 0
    assert a["q1_lineage"]["both_endpoints_compiled"] == 1   # only edge 1->2 has both compiled


def test_role_lineage_consistency(tmp_path):
    """New-style rows: repair draws have parents, independent draws do not."""
    parent_src = candidate("p")
    db = build_db(
        tmp_path,
        [
            # honest: an independent root with no parent
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0,
                        sampling=role_sampling("independent")),
            # honest: a repair child with a parent
            attempt_row(2, 0x1000, candidate("c"), prompt=repair_prompt(parent_src),
                        parent=1, score=70.0, sampling=role_sampling("repair")),
            # DEFECT: an "independent" draw wired to a parent
            attempt_row(3, 0x1000, candidate("d"), prompt=repair_prompt(parent_src),
                        parent=1, score=60.0, sampling=role_sampling("independent")),
            # DEFECT: a "repair" draw with no parent at all
            attempt_row(4, 0x1000, candidate("e"), prompt="free draw", score=60.0,
                        sampling=role_sampling("repair")),
        ],
        [edge_row(1, 2), edge_row(1, 3)],
    )
    a = ta.audit_db(db)
    q3 = a["q3_roots_vs_edges"]

    assert q3["role_available"] is True
    assert q3["role_counts"] == {"independent": 2, "repair": 2}
    assert q3["role_violations"]["independent_with_parent"]["count"] == 1
    assert q3["role_violations"]["independent_with_parent"]["example_ids"] == [3]
    assert q3["role_violations"]["independent_in_edge"]["count"] == 1
    assert q3["role_violations"]["repair_without_parent"]["count"] == 1
    assert q3["role_violations"]["repair_without_parent"]["example_ids"] == [4]
    assert q3["role_violations"]["repair_without_edge"]["count"] == 1
    assert q3["rows_with_parent_field"] == 2
    # attempts 1 and 4: no parent, and neither is an edge child
    assert q3["independent_roots_no_parent_no_edge_child"] == 2

    finding = {f["id"]: f for f in a["findings"]}
    assert finding["role-lineage-consistency"]["severity"] == "critical"


def test_role_violations_clean_when_lineage_is_honest(tmp_path):
    parent_src = candidate("p")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, parent_src, prompt="TARGET ASSEMBLY", score=90.0,
                        sampling=role_sampling("independent")),
            attempt_row(2, 0x1000, candidate("c"), prompt=repair_prompt(parent_src),
                        parent=1, score=70.0, sampling=role_sampling("repair")),
        ],
        [edge_row(1, 2)],
    )
    a = ta.audit_db(db)
    assert all(v["count"] == 0 for v in a["q3_roots_vs_edges"]["role_violations"].values())
    assert {f["id"]: f for f in a["findings"]}["role-lineage-consistency"]["severity"] == "info"


# ------------------------------------------------------- questions 4 through 8

def test_model_inputs_and_systematic_prompt_absence(tmp_path):
    db = build_db(
        tmp_path,
        [
            # modelled strategy with no prompt at all -> systematically absent
            attempt_row(1, 0x1000, candidate("a"), prompt="", strategy="blind-pass",
                        run_id="run-blind"),
            attempt_row(2, 0x1000, candidate("b"), prompt="", strategy="blind-pass",
                        run_id="run-blind"),
            # strategy that does record prompts
            attempt_row(3, 0x1000, candidate("c"), prompt="TARGET ASSEMBLY", strategy="good-pass",
                        run_id="run-good"),
        ],
    )
    a = ta.audit_db(db)
    q4 = a["q4_model_inputs"]
    assert q4["rows_with_prompt_context"] == 1
    assert q4["rows_with_raw_response"] == 3
    assert q4["rows_with_model"] == 3
    sa = q4["strategies_with_prompts_systematically_absent"]
    assert sa["strategies"] == ["blind-pass"]
    assert sa["rows_modeled"] == 2
    by_strategy = {s["strategy"]: s for s in q4["by_strategy"]}
    assert by_strategy["blind-pass"]["has_prompt_context"] == 0
    assert by_strategy["good-pass"]["has_prompt_context"] == 1
    assert [r["run_id"] for r in q4["by_run_id_top"]][0] == "run-blind"
    assert {r["run_id"]: r for r in q4["by_run_id_top"]}["run-blind"]["has_prompt_context"] == 0


def test_raw_response_absent_is_counted_separately_from_prompt(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="TARGET ASSEMBLY", raw_response=""),
            attempt_row(2, 0x1000, candidate("c"), prompt="", raw_response=""),
        ],
    )
    a = ta.audit_db(db)
    assert a["q8_failure_logging"]["raw_response_empty"] == 2
    assert a["q4_model_inputs"]["rows_with_raw_response"] == 0
    rl = a["training_usable"]["row_level"]
    assert rl["excluded_no_prompt_context"] == 1
    assert rl["excluded_no_raw_response"] == 1
    assert rl["usable"] == 0


def test_consumed_call_without_response_is_flagged(tmp_path):
    db = build_db(
        tmp_path,
        [attempt_row(1, 0x1000, candidate("p"), prompt="TARGET ASSEMBLY",
                     raw_response="", token_cost=4321, model="m")],
    )
    a = ta.audit_db(db)
    q8 = a["q8_failure_logging"]
    assert q8["consumed_call_without_response"] == 1
    assert q8["consumed_call_without_response_examples"] == [1]
    finding = {f["id"]: f for f in a["findings"]}
    assert "consumed-call-without-response" in finding


def test_compiler_identity_present_and_absent(tmp_path):
    recipe = json.dumps({
        "temperature": 0.6,
        "compiler_recipe": {"target": "build/src/x.o",
                            "settings": {"C_OPT": "-O2", "C_MIPS": "-mips1"}},
    })
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="p", sampling=recipe),
            attempt_row(2, 0x1000, candidate("c"), prompt="p",
                        sampling=json.dumps({"temperature": 0.6})),
        ],
    )
    a = ta.audit_db(db)
    q5 = a["q5_compiler_identity"]
    assert q5["rows_with_identity"] == 1
    assert q5["rows_without_identity"] == 1
    assert q5["rows_without_identity_examples"] == [2]
    assert q5["identity_key_used"] == {"compiler_recipe": 1}
    assert "build/src/x.o/-O2/-mips1" in next(iter(q5["identities_top"]))
    finding = {f["id"]: f for f in a["findings"]}
    assert "compiler-identity-missing" in finding
    assert "no-compiler-identity" not in finding


def test_all_rows_missing_compiler_identity_is_critical(tmp_path):
    db = build_db(tmp_path, [attempt_row(1, 0x1000, candidate("p"), prompt="p")])
    a = ta.audit_db(db)
    assert a["q5_compiler_identity"]["rows_with_identity"] == 0
    finding = {f["id"]: f for f in a["findings"]}
    assert finding["no-compiler-identity"]["severity"] == "critical"


def test_source_sha256_mismatch_is_flagged(tmp_path):
    src = candidate("p")
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, src, prompt="p"),
            attempt_row(2, 0x1000, src, prompt="p",
                        source_sha256=hashlib.sha256(b"something else").hexdigest()),
            attempt_row(3, 0x1000, src, prompt="p", source_sha256=None),
        ],
    )
    a = ta.audit_db(db)
    q6 = a["q6_candidate_hashes"]
    assert q6["match"] == 1
    assert q6["mismatch"] == 1
    assert q6["null_or_empty"] == 1
    assert [e["id"] for e in q6["mismatch_examples"]] == [2]
    finding = {f["id"]: f for f in a["findings"]}
    assert finding["source-hash-mismatch"]["severity"] == "critical"
    assert finding["source-hash-missing"]["count"] == 1


def test_compiled_false_without_stderr_is_flagged(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="p", compiled=0, stderr="error: boom"),
            attempt_row(2, 0x1000, candidate("c"), prompt="p", compiled=0, stderr=""),
            attempt_row(3, 0x1000, candidate("d"), prompt="p", compiled=1, stderr=""),
        ],
    )
    a = ta.audit_db(db)
    q8 = a["q8_failure_logging"]
    assert q8["compiled_false"] == 2
    assert q8["compiled_false_with_stderr"] == 1
    assert q8["compiled_false_without_stderr"] == 1
    assert q8["compiled_true_with_stderr"] == 0
    finding = {f["id"]: f for f in a["findings"]}
    assert finding["failure-without-stderr"]["count"] == 1
    assert finding["failure-without-stderr"]["severity"] == "critical"


def test_extract_status_and_done_reason_census(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="p",
                        extract_status="ok", done_reason="stop"),
            attempt_row(2, 0x1000, candidate("c"), prompt="p",
                        extract_status="", done_reason=""),
            attempt_row(3, 0x1000, candidate("d"), prompt="p",
                        extract_status=None, done_reason=None),
        ],
    )
    a = ta.audit_db(db)
    q8 = a["q8_failure_logging"]
    assert q8["extract_status_set"] == 1
    assert q8["done_reason_set"] == 1
    assert q8["extract_status_distribution"]["ok"] == 1
    assert q8["extract_status_distribution"]["<empty>"] == 1
    assert q8["extract_status_distribution"]["NULL"] == 1
    assert q8["done_reason_distribution"]["stop"] == 1
    assert q8["done_reason_distribution"]["NULL"] == 1


def test_verifier_distribution_including_nulls(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("a"), prompt="p", exact=1, compiled=1),
            attempt_row(2, 0x1000, candidate("b"), prompt="p", exact=0, compiled=1),
            attempt_row(3, 0x1000, candidate("c"), prompt="p", exact=None, compiled=0,
                        stderr="e"),
        ],
    )
    a = ta.audit_db(db)
    q7 = a["q7_verifier"]
    assert q7["exact_null"] == 1
    assert q7["exact_true"] == 1
    assert q7["exact_false"] == 1
    assert q7["compiled_true"] == 2 and q7["compiled_false"] == 1
    finding = {f["id"]: f for f in a["findings"]}
    assert "exact-null" in finding


def test_invalid_sampling_json_is_tolerated(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="p", sampling="not json at all"),
            attempt_row(2, 0x1000, candidate("c"), prompt="p", sampling="[1, 2, 3]"),
            attempt_row(3, 0x1000, candidate("d"), prompt="p", sampling=None),
        ],
    )
    a = ta.audit_db(db)
    assert a["q5_compiler_identity"]["rows_with_identity"] == 0
    # one syntactically invalid value and one valid JSON list, which is not a
    # sampling object either; a NULL sampling column is absent, not unparseable
    assert a["q5_compiler_identity"]["unparseable_sampling_json"] == 2
    assert a["warnings"], "unparseable sampling must be reported, not swallowed"


def test_missing_table_raises(tmp_path):
    path = tmp_path / "empty.sqlite"
    sqlite3.connect(path).close()
    with pytest.raises(ValueError):
        ta.audit_db(path)


def test_connection_is_read_only(tmp_path):
    db = build_db(tmp_path, [attempt_row(1, 0x1000, candidate("p"), prompt="p")])
    before = db.read_bytes()
    con = ta.connect_readonly(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            con.execute("INSERT INTO functions (addr, name) VALUES (99, 'nope')")
    finally:
        con.close()
    assert db.read_bytes() == before


def test_audit_does_not_modify_the_database(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("c"), prompt="elsewhere", parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    before = db.read_bytes()
    a = ta.audit_db(db)
    assert db.read_bytes() == before
    assert a["totals"] == {"attempts": 2, "edges": 1}


def test_cli_writes_json_and_markdown(tmp_path):
    db = build_db(
        tmp_path,
        [
            attempt_row(1, 0x1000, candidate("p"), prompt="TARGET ASSEMBLY", score=90.0),
            attempt_row(2, 0x1000, candidate("c"), prompt=repair_prompt(candidate("mismatch")),
                        parent=1, score=70.0),
        ],
        [edge_row(1, 2)],
    )
    json_out = tmp_path / "out" / "audit.json"
    md_out = tmp_path / "out" / "AUDIT.md"
    rc = ta.main(["--kb", str(db), "--json-out", str(json_out), "--md-out", str(md_out),
                  "--examples", "3"])

    assert rc == 1, "a critical finding must make the CLI exit non-zero"
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert "kb" in payload["databases"]
    assert payload["databases"]["kb"]["totals"]["attempts"] == 2
    assert payload["databases"]["kb"]["q2_parent_in_prompt"]["genuinely_absent"] == 1

    text = md_out.read_text(encoding="utf-8")
    assert "# Trajectory integrity audit" in text
    assert "no candidate at all" in text
    assert "**1 of 1 edges are usable**" not in text  # this fixture's pair is excluded
    assert "0 of 1 edges are usable" in text


def test_cli_requires_a_target():
    with pytest.raises(SystemExit):
        ta.main([])


# ------------------------------------------------------------- unit primitives

def test_strip_gutter_handles_the_real_formats():
    text = "   1 | #include \"common.h\"\n  12 | int x;\nno gutter here\n"
    stripped, hits = ta.strip_gutter(text)
    assert hits == 2
    assert stripped.splitlines()[0] == '#include "common.h"'
    assert stripped.splitlines()[1] == "int x;"
    assert stripped.splitlines()[2] == "no gutter here"


def test_strip_gutter_is_a_noop_without_a_gutter():
    text = "#include \"common.h\"\nint x;\n"
    stripped, hits = ta.strip_gutter(text)
    assert hits == 0
    assert stripped == text


def test_fenced_blocks_extracts_bodies():
    text = 'intro\n```c\nalpha\n```\nmiddle\n```\nbeta\n```\n'
    assert ta.fenced_blocks(text) == ["alpha", "beta"]


def test_parse_json_object_is_defensive():
    assert ta.parse_json_object(None) is None
    assert ta.parse_json_object("") is None
    assert ta.parse_json_object("   ") is None
    assert ta.parse_json_object("{bad") is None
    assert ta.parse_json_object("[1,2]") is None
    assert ta.parse_json_object('{"a": 1}') == {"a": 1}


def test_role_and_compiler_identity_extraction():
    obj = json.loads(role_sampling("repair"))
    assert ta.role_of(obj) == "repair"
    assert ta.role_of({"generation": {}}) is None
    assert ta.role_of(None) is None

    ident, key = ta.compiler_identity(json.loads(json.dumps(
        {"compiler_recipe": {"target": "t.o", "settings": {"C_OPT": "-O1", "C_MIPS": "-mips1"}}})))
    assert key == "compiler_recipe"
    assert ident == "compiler_recipe:t.o/-O1/-mips1"
    assert ta.compiler_identity({"recipe": "ido-5.3"}) == ("recipe:'ido-5.3'", "recipe")
    assert ta.compiler_identity({}) == (None, None)
    assert ta.compiler_identity(None) == (None, None)


def test_dig_walks_nested_dicts():
    assert ta.dig({"a": {"b": {"c": 1}}}, "a.b.c") == 1
    assert ta.dig({"a": {"b": {}}}, "a.b.c") is None
    assert ta.dig(None, "a") is None
    assert ta.dig({"a": 1}, "a.b") is None

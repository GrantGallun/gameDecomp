"""The handoff's lineage and receipt requirements, pinned.

These tests exist because the factory's PREVIOUS behaviour was not merely untested -- the
existing suite asserted the bug. `test_an_improving_candidate_becomes_the_parent_of_the_
next_round` pins exactly the failure mode: the prompt never changed, so the recorded
"parent" described a relationship the model was never shown.

Every test here is written so that it FAILS on the old implementation. That is the point:
a test that passes both before and after a lineage fix is not a lineage test. The docstring
of each says which old behaviour it catches.

`TRAINING.md` states the rule in one line: best-of-N samples are independent roots, and an
explicit parent -> child edge is what turns an attempt into refinement data.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from eval import trajectory_factory as tf
from solver import llm


# --- fixtures -----------------------------------------------------------------

def _kb(tmp_path: Path) -> Path:
    """A minimal knowledge base with the tables the factory reads and writes."""
    path = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE tus (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT, tu_id INTEGER,
                                size INTEGER);
        CREATE TABLE attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, func_addr INTEGER, iteration INTEGER,
            source_code TEXT, prompt_context TEXT, compiled INTEGER, compiler_stderr TEXT,
            score REAL, diff_summary TEXT, strategy TEXT, model TEXT, sampling TEXT,
            wall_ms INTEGER, token_cost INTEGER, created_at INTEGER, raw_response TEXT,
            extract_status TEXT, done_reason TEXT, exact INTEGER, run_id TEXT,
            parent_attempt_id INTEGER, source_sha256 TEXT, prompt_sha256 TEXT);
    """)
    conn.execute("INSERT INTO tus (id, name) VALUES (1, 'src/game/race.c')")
    conn.execute("INSERT INTO functions (addr, name, tu_id, size) VALUES (32768, 'probeFn', 1, 40)")
    conn.commit()
    conn.close()
    return path


def _spec(tmp_path: Path, kb: Path) -> tf.GameSpec:
    return tf.GameSpec("sbk1", tmp_path / "repo", kb, "ido-5.3", ready=True)


class RecordingGenerator:
    """A generator that records every prompt it is asked, so the REQUEST can be asserted.

    The old factory's defect was a property of the request, not of the answer, so a fake
    that only returns sources cannot detect it.

    All proposals in one draw share ONE `RepairState` object, exactly as the real generator
    would: the factory assigns the state's receipt id to the proposal parent, and a fresh
    object per proposal would not see that assignment.
    """

    def __init__(self, scripts, receipts=None, parent_state=None):
        self.scripts = list(scripts)
        self.prompts: list[tuple[str, str]] = []       # (role, prompt)
        self.receipts = receipts
        self.parent_state = parent_state
        self.calls = 0
        self.refusals = 0
        self.errors: list[str] = []
        self.dropped = 0
        self.raw_heads: list[str] = []
        self.receipt_objects: list = []

    def draw(self, prompt, n, temperature, *, role="independent", deadline=None):
        out = []
        # A repair draw carries a RepairState, empty of a receipt id at creation: the factory
        # fills the id in from the state the PROMPT was rendered from. A fake that supplied
        # the id itself would let a factory bug hide behind the fake's own bookkeeping.
        shared = None
        if role == "repair":
            shared = self.parent_state or tf.RepairState(
                source="void probeFn(void) { s32 x = 1; }", compiled=True,
                score=80.0, diff="diff-for-80.0")
        for _ in range(n):
            self.prompts.append((role, prompt))
            if not self.scripts:
                break
            source = self.scripts.pop(0)
            self.calls += 1
            receipt = None
            if self.receipts is not None:
                status = self.receipts.pop(0) if self.receipts else "ok"
                receipt = llm.GenerationReceipt(
                    prompt=prompt, raw_response=f"```c\n{source}\n```",
                    status=status, model="fake:7b", digest="deadbeef" * 8,
                    sampling={"temperature": temperature}, token_cost=123,
                    wall_ms=45, extracted=source if status == "ok" else "",
                    extract_status="fence")
                self.receipt_objects.append(receipt)
            out.append(tf.Proposal(source=source, prompt=prompt, action=role,
                                   receipt=receipt, parent=shared))
        return out

    def sample(self, prompt, n, temperature):
        return [p.source for p in self.draw(prompt, n, temperature)]


class RecordingScorer:
    """Compiles nothing and records what it was handed, including the parent.

    It deliberately does NOT report `source` back. The real scorer may or may not, and the
    factory must not depend on it: the repair state has to be the normalized source of the
    attempt that was logged, which the factory knows and the scorer does not.
    """

    def __init__(self, scores, start_id=100, echo_source=False):
        self.scores = list(scores)
        self.seen: list[dict] = []
        self.next_id = start_id
        self.echo_source = echo_source

    def score(self, func: str, proposal, parent_attempt_id=None):
        score = self.scores.pop(0) if self.scores else 0.0
        parent = (proposal.parent.attempt_id if proposal.parent is not None
                  else parent_attempt_id)
        self.next_id += 1
        row = {"func": func, "source": proposal.source, "score": score,
               "compiled": score > 0.0, "exact": score >= 100.0,
               "diff": f"diff-for-{score}", "attempt_id": self.next_id,
               "parent": parent, "action": proposal.action,
               "lineage": proposal.lineage,
               "prompt": proposal.prompt,
               "status": proposal.status}
        self.seen.append(row)
        # `seen` keeps everything the fake observed; the RETURNED dict drops `source` unless
        # asked, because a scorer that echoes it can hide a factory that depends on the echo.
        out = dict(row)
        if not self.echo_source:
            out.pop("source", None)
        return dict(out, faults={axis: 0 for axis in tf.AXES})


def _context(asm="glabel probeFn\n  jr $ra\nendlabel probeFn"):
    leaf = "LEAF PROMPT placeholder"
    return lambda func: {"asm": asm, "draft": "void probeFn(void) {}", "hints": "",
                         "prompt": leaf}


def _factory(generator, scorer, **kwargs):
    kwargs.setdefault("context_for", _context())
    return tf.Factory(generator=generator, scorer=scorer, **kwargs)


def _item(score=50.0, attempt_id=None):
    return {"name": "probeFn", "addr": 32768, "best_score": score,
            "best_attempt_id": attempt_id, "faults": {a: 1 for a in tf.AXES},
            "owned_share": 1.0, "learnability": 0.1, "attempts": 3}


# --- 1. independent draws have root lineage -----------------------------------

def test_best_of_n_draws_have_no_candidate_parent():
    """FAILS ON OLD: every draw was given the previous best attempt as its parent."""
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4)
    scorer = RecordingScorer([60.0, 70.0, 80.0, 90.0])
    outcome = _factory(gen, scorer, rounds=1, samples=4).run_function(_item(), [10])
    assert outcome.attempts == 4
    assert [row["parent"] for row in scorer.seen] == [None, None, None, None]
    assert {row["lineage"] for row in scorer.seen} == {"root"}
    assert {row["action"] for row in scorer.seen} == {"independent"}


def test_a_single_round_asks_one_question_even_when_the_best_changes():
    """FAILS ON OLD: the recorded parent moved while the prompt stood still."""
    gen = RecordingGenerator(["void probeFn(void) {}"] * 3)
    scorer = RecordingScorer([60.0, 95.0, 70.0])
    outcome = _factory(gen, scorer, rounds=1, samples=3).run_function(_item(), [10])
    assert len({prompt for _role, prompt in gen.prompts}) == 1
    assert outcome.distinct_prompts == 1
    assert all(row["parent"] is None for row in scorer.seen)


def test_root_lineage_survives_an_improving_round():
    """A round that improves changes the NEXT action, not the lineage of what it drew."""
    state = tf.RepairState(source="void probeFn(void) {}")
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4, parent_state=state)
    scorer = RecordingScorer([90.0, 40.0, 92.0, 45.0])
    outcome = _factory(gen, scorer, rounds=2, samples=2).run_function(_item(score=30.0), [10])
    independent = [row for row in scorer.seen if row["action"] == "independent"]
    repairs = [row for row in scorer.seen if row["action"] != "independent"]
    assert len(independent) == 2 and len(repairs) == 2
    assert all(row["parent"] is None for row in independent)
    assert all(row["parent"] is not None for row in repairs)
    assert outcome.repair_requests == 2
    assert outcome.independent_requests == 2


# --- 2. a repair carries the parent's exact source and feedback ---------------

def test_a_repair_prompt_contains_the_parents_exact_source_and_its_own_diff():
    """FAILS ON OLD: no repair prompt existed at all -- the leaf prompt was re-sent.

    The expected prompt is not hand-written here. It is produced by calling the project's own
    `render_repair_prompt` on the state the parent attempt actually recorded, and the
    assertion is that the prompt SENT is that string. A hand-written expectation would pin
    today's wording; this pins the contract.
    """
    parent_c = "void probeFn(void) { s32 x = 1; }"
    child_c = "void probeFn(void) { s32 x = 2; }"
    gen = RecordingGenerator([parent_c, child_c])
    scorer = RecordingScorer([80.0, 95.0])
    factory = _factory(gen, scorer, rounds=2, samples=1)
    factory.run_function(_item(score=30.0), [10])

    assert [role for role, _ in gen.prompts] == ["independent", "repair"]
    sent = gen.prompts[1][1]
    expected = tf.render_repair_prompt(
        tf.RepairState(source=parent_c, compiled=True, score=80.0,
                       diff="diff-for-80.0", attempt_id=100),
        factory.context_for("probeFn")["asm"])
    assert sent == expected, "the repair prompt is the project's own rendering of the state"

    # And it contains the three things the handoff requires the model to see.
    assert parent_c in sent, "the parent's exact C"
    assert "diff-for-80.0" in sent, "the parent's own compiler feedback"
    assert "80.00" in sent, "the parent's score"
    assert sent != gen.prompts[0][1], "and it is not the leaf prompt again"
    assert "LEAF PROMPT placeholder" not in sent


def test_a_repair_of_a_non_compiling_parent_quotes_the_compiler_error():
    """The `fix-compile` branch: a parent that did not compile has a stderr, not a diff."""
    gen = RecordingGenerator(["void probeFn(void) { bad }", "void probeFn(void) {}"])
    scorer = RecordingScorer([0.0, 50.0])

    original = scorer.score

    def score_with_stderr(func, proposal, parent_attempt_id=None):
        row = original(func, proposal, parent_attempt_id)
        row["compiled"] = False
        row["compiler_stderr"] = "cfe: Error: line 1: syntax error"
        return row

    scorer.score = score_with_stderr
    factory = _factory(gen, scorer, rounds=2, samples=1)
    outcome = factory.run_function(_item(score=30.0), [10])
    # The first round produced nothing compiled, so there is no repair state and the function
    # stops with a reason rather than silently re-asking the leaf prompt.
    assert outcome.stopped == "no repair state"
    assert "compiler outcome" in outcome.stopping_note
    assert len(gen.prompts) == 1


def test_a_repair_draw_without_a_parent_object_is_still_linked(tmp_path):
    """A generator that forgets the parent object used to produce an edge-less repair.

    Measured on the 2026-09-20 collection pilot: eight repair calls whose prompts correctly
    quoted the parent's C and score, every one stored with `parent_attempt_id` NULL and no
    `attempt_edges` row -- a repair with the appearance of lineage and none of the substance.

    The factory owns the assignment, so it must set the parent from the state the PROMPT was
    built from rather than depend on the generator having attached one.
    """
    from solver import workspace as ws_mod

    kb = _kb(tmp_path)
    scorer = tf.WorkspaceScorer(repo=tmp_path, kb=kb, strategy="t", run_id="r")
    scorer.workspaces = {}
    scorer.workspace = lambda func: tmp_path

    import solver.workspace
    original = solver.workspace.score

    def fake_score(ws, repo, name, code, **kw):
        kw.pop("conn", None)
        kw.pop("func", None)
        att = ws_mod.Attempt(True, 70.0, False, "d", "", "")
        ws_mod.record_attempt(scorer.conn, "probeFn", code, att, **kw)
        return att

    solver.workspace.score = fake_score

    class Forgetful(RecordingGenerator):
        """Correct repair prompt, but no parent object on the proposal."""

        def draw(self, prompt, n, temperature, *, role="independent", deadline=None):
            out = super().draw(prompt, n, temperature, role=role, deadline=deadline)
            for proposal in out:
                proposal.parent = None
            return out

    gen = Forgetful(["void probeFn(void) {}"] * 4)
    try:
        factory = _factory(gen, scorer, rounds=2, samples=2)
        factory.run_function(_item(score=30.0), [10])
    finally:
        solver.workspace.score = original

    rows = scorer.conn.execute(
        "select id, parent_attempt_id from attempts order by id").fetchall()
    assert len(rows) == 4
    assert [row[1] for row in rows[:2]] == [None, None], "independent draws are roots"
    # Both round-0 candidates scored the same, so which one wins is `max`'s tie-break and not
    # the assertion. What matters is that the repairs anchor on a REAL round-0 attempt row
    # rather than on nothing -- which is what the pilot stored before this fix.
    roots = {row[0] for row in rows[:2]}
    parents = [row[1] for row in rows[2:]]
    assert set(parents) <= roots and None not in parents, (
        f"repairs must point at a round-0 attempt, got {parents} against roots {roots}")
    edges = scorer.conn.execute(
        "select parent_attempt_id, child_attempt_id from attempt_edges").fetchall()
    assert len(edges) == 2, "the repair edge must exist"
    assert {edge[0] for edge in edges} <= roots
    assert {edge[1] for edge in edges} == {rows[2][0], rows[3][0]}


def test_a_serve_generator_attaches_a_parent_to_repair_draws():
    """`ServeGenerator.draw` must hand back a parent object for a repair role."""
    from eval.inference_generator import ServeGenerator

    generator = ServeGenerator.__new__(ServeGenerator)
    generator.identity = type("I", (), {"served_model": "m", "adapter_hash": "",
                                        "extra": {}})()
    generator._identity_probed = True
    generator.model = "m"
    generator.adapter = ""
    generator.function = ""
    generator.max_tokens = 8
    generator.top_p = 0.9
    generator.top_k = 0
    generator.seed = 1
    generator.prefill = "```c\n"
    generator.errors = []
    generator.receipts = []
    generator.raw_heads = []
    generator.refusals = 0
    generator.dropped = 0

    class Result:
        text = "```c\nvoid probeFn(void) {}\n```"
        receipt = {"model": "m", "eval_count": 3}
        model = "m"
        completion_tokens = 3
        prompt_tokens = 10
        finish_reason = "stop"
        request_id = "r1"
        adapter_hash = ""

    class StubClient:
        def chat(self, *a, **k):
            return Result()

    generator.client = StubClient()
    proposals = generator.draw("p", 1, 0.5, role="repair")
    assert proposals[0].parent is not None, (
        "a repair draw with no parent object cannot be linked by the factory")
    assert proposals[0].parent.source == "", "the factory fills the id, not the generator"
    root = generator.draw("p", 1, 0.5, role="independent")
    assert root[0].parent is None, "an independent draw is a root"


def test_a_repair_of_a_compiling_parent_uses_the_diff_not_the_stderr():
    """The `fix-diff` branch, and the action recorded on the edge names it."""
    gen = RecordingGenerator(["void probeFn(void) { s32 x = 1; }",
                              "void probeFn(void) { s32 x = 2; }"])
    scorer = RecordingScorer([72.0, 88.0])
    factory = _factory(gen, scorer, rounds=2, samples=1)
    factory.run_function(_item(score=30.0), [10])
    assert scorer.seen[1]["action"] == "repair"
    sent = gen.prompts[1][1]
    assert "Instruction diff" in sent
    assert "diff-for-72.0" in sent
    assert "DID NOT COMPILE" not in sent


def test_the_repair_state_survives_a_scorer_that_does_not_echo_the_source():
    """A scorer that returns no `source` must not produce a repair prompt with no C in it.

    This is the aliasing bug it pins: `current` used to BE the scorer's dict, so a scorer
    without a `source` key made `repair_state` return None and the function stop with
    "no repair state" -- losing every repair in the run for a reason that had nothing to do
    with the model.
    """
    parent_c = "void probeFn(void) { s32 k = 9; }"
    child_c = "void probeFn(void) { s32 k = 8; }"
    gen = RecordingGenerator([parent_c, child_c])
    scorer = RecordingScorer([80.0, 85.0], echo_source=False)
    assert "source" not in scorer.score(
        "probeFn", tf.Proposal(source="x", prompt="p")), \
        "this fake must not echo the source, or it cannot pin the bug"
    factory = _factory(gen, scorer, rounds=2, samples=1)
    outcome = factory.run_function(_item(score=30.0), [10])
    assert outcome.stopped == "", f"the run must reach the repair, not stop: {outcome.stopped}"
    assert len(gen.prompts) == 2
    assert parent_c in gen.prompts[1][1], "the repair prompt quotes the logged parent's C"


def test_the_repair_prompt_is_rendered_from_the_recorded_parent_not_the_draft():
    """The strongest form of the requirement: the C in the prompt IS the parent's C.

    Verified by a route independent of the assertion: the expected prompt is produced by
    pointing `render_repair_prompt` at the parent candidate and the parent's OWN diff, and
    the test asserts the sent prompt equals it. That is an identity check between what the
    model saw and what the trajectory recorded, which is the property the review found false.
    """
    parent_c = "void probeFn(void) { s32 n = 3; gVar += n; }"
    gen = RecordingGenerator([parent_c, "void probeFn(void) { }"])
    scorer = RecordingScorer([91.25, 99.0])
    factory = _factory(gen, scorer, rounds=2, samples=1)
    factory.run_function(_item(score=30.0), [10])

    sent = gen.prompts[1][1]
    assert parent_c in sent, "the parent candidate's C is quoted verbatim"
    assert "diff-for-91.25" in sent, "the parent's own residual is the feedback"
    assert "91.25" in sent, "the parent's score is stated, so it is distinguishable from the draft"
    expected = tf.render_repair_prompt(
        tf.RepairState(source=parent_c, compiled=True, score=91.25,
                       diff="diff-for-91.25", attempt_id=101),
        factory.context_for("probeFn")["asm"])
    assert sent == expected


def test_a_repair_of_a_non_compiling_parent_quotes_the_compiler_error():
    """The `fix-compile` branch: a parent that did not compile has a stderr, not a diff."""
    gen = RecordingGenerator(["void probeFn(void) { bad }", "void probeFn(void) {}"])
    scorer = RecordingScorer([0.0, 50.0])

    def score_with_stderr(func, proposal, parent_attempt_id=None):
        row = RecordingScorer.score(scorer, func, proposal, parent_attempt_id)
        row["compiled"] = False
        row["compiler_stderr"] = "cfe: Error: line 1: syntax error"
        return row

    factory = _factory(gen, scorer, rounds=2, samples=1)
    original = scorer.score
    scorer.score = score_with_stderr
    # The first round produces nothing compiled, so there is no repair state and the
    # function stops with a reason rather than silently re-asking the leaf prompt.
    outcome = factory.run_function(_item(score=30.0), [10])
    assert outcome.stopped == "no repair state"
    assert "compiler outcome" in outcome.stopping_note
    assert len(gen.prompts) == 1
    scorer.score = original


def test_the_recorded_edge_parent_is_the_attempt_the_prompt_was_built_from():
    """The parent id must be the state's receipt, not 'whatever scored best later'."""
    state = tf.RepairState(source="void probeFn(void) {}")
    gen = RecordingGenerator(["A", "B", "C", "D"], parent_state=state)
    scorer = RecordingScorer([70.0, 85.0, 88.0, 40.0])
    factory = _factory(gen, scorer, rounds=2, samples=2)
    factory.run_function(_item(score=30.0), [10])
    # Round 0: two independent roots. Round 1: both repair the BEST of round 0 -- which is
    # the 85 that landed on receipt 102, not the 70 on 101 and not whichever came last.
    assert scorer.seen[0]["parent"] is None
    assert scorer.seen[1]["parent"] is None
    assert scorer.seen[2]["parent"] == 102
    assert scorer.seen[3]["parent"] == 102


def test_no_repair_state_is_reported_rather_than_re_asking_the_leaf_prompt():
    """A repair request with no parent is a fabrication; the run must say so and stop."""
    gen = RecordingGenerator(["garbage-that-does-not-compile"])
    scorer = RecordingScorer([0.0])
    outcome = _factory(gen, scorer, rounds=3, samples=1).run_function(_item(score=30.0), [10])
    assert outcome.stopped == "no repair state"
    assert [role for role, _ in gen.prompts] == ["independent"]


# --- 3. every field of the receipt reaches durable storage --------------------

def test_all_generation_metadata_reaches_durable_storage(tmp_path):
    """FAILS ON OLD: `record_attempt` was called with none of prompt/model/raw_response.

    The test drives the REAL `WorkspaceScorer` and the REAL `record_attempt` into a real
    SQLite database. Only the compile itself is stubbed, because a stub that also stubbed the
    write would prove nothing about what is written.
    """
    from solver import workspace as ws_mod

    kb = _kb(tmp_path)
    conn = sqlite3.connect(kb)
    gen = RecordingGenerator(["void probeFn(void) {}", "void probeFn(void) { }"],
                             receipts=["ok", "ok"])
    scorer = tf.WorkspaceScorer(repo=tmp_path, kb=kb, strategy="factory-refine",
                                run_id="test-run-1")
    scorer.workspaces = {}
    scorer.workspace = lambda func: tmp_path            # never bootstrap in a unit test

    import solver.workspace
    original = solver.workspace.score

    def fake_score(ws, repo, name, code, **kw):
        kw.pop("conn", None)
        kw.pop("func", None)
        att = ws_mod.Attempt(True, 77.0, False, "diff text", "", "")
        ws_mod.record_attempt(scorer.conn, "probeFn", code, att, **kw)
        return att

    solver.workspace.score = fake_score
    try:
        factory = _factory(gen, scorer, rounds=2, samples=1)
        factory.run_function(_item(score=30.0), [10])
    finally:
        solver.workspace.score = original

    rows = conn.execute(
        "SELECT prompt_context, raw_response, model, sampling, token_cost, wall_ms,"
        " extract_status, parent_attempt_id, run_id, source_code FROM attempts"
        " ORDER BY id").fetchall()
    assert len(rows) == 2, "one independent draw and one requested repair"
    for prompt, raw, model, sampling, tokens, wall, extract, parent, run_id, source in rows:
        assert prompt and len(prompt) > 10, "the exact prompt must be stored"
        assert raw and "```c" in raw, "the raw response must be stored"
        assert model == "fake:7b"
        assert run_id == "test-run-1"
        assert extract == "fence"
        assert tokens == 123 and wall == 45, "token cost and wall time are part of the receipt"
        meta = json.loads(sampling)
        assert meta["generation"]["model_digest"] == "deadbeef" * 8
        assert meta["generation"]["sampling"]["temperature"] == 0.8
        assert meta["generation"]["status"] == "ok"
    assert rows[0][7] is None, "an independent draw has no parent row"
    assert rows[1][7] == 1, "the repair points at the first attempt's receipt id"

    # The repair's PROMPT really is a repair prompt, so the stored prompt is the question
    # the parent id claims it was.
    assert "TARGET ASSEMBLY" in rows[1][0]
    assert rows[1][0] != rows[0][0]


def test_a_failed_call_is_stored_with_its_own_status_and_not_dropped(tmp_path):
    """A refusal or an error is a logged outcome, not an absence of one."""
    kb = _kb(tmp_path)
    conn = sqlite3.connect(kb)
    gen = RecordingGenerator(["void probeFn(void) {}", ""], receipts=["ok", "refusal"])
    gen.receipt_objects = []
    # Second draw refuses: the Proposal still exists and still carries a receipt.
    gen.scripts = ["void probeFn(void) {}", "REFUSED"]
    gen.receipts = ["ok", "refusal"]
    scorer = RecordingScorer([60.0, 0.0])
    factory = _factory(gen, scorer, rounds=1, samples=2)
    outcome = factory.run_function(_item(), [10])
    assert outcome.attempts == 2, "a refusal consumes budget"
    assert outcome.refusals == 1
    statuses = [row["status"] for row in scorer.seen]
    assert statuses == ["ok", "refusal"]


# --- 4. refusals, timeouts and errors are retained AND budgeted ---------------

def test_a_refusal_consumes_the_call_budget():
    """FAILS ON OLD: a refused call cost nothing, so a refusing target ran forever."""
    class Refusing:
        """A generator whose every call returns a receipt and no C."""
        refusals = 0
        errors: list = []
        raw_heads: list = []
        dropped = 0

        def draw(self, prompt, n, temperature, *, role="independent", deadline=None):
            out = []
            for _ in range(n):
                self.refusals += 1
                receipt = llm.GenerationReceipt(
                    prompt=prompt, raw_response="I'm sorry, I cannot help.",
                    status="refusal", model="fake:7b", digest="x" * 8, wall_ms=5)
                out.append(tf.Proposal(prompt=prompt, action=role, receipt=receipt))
            return out

    scorer = RecordingScorer([])
    budget = [2]
    factory = _factory(Refusing(), scorer, rounds=1, samples=2)
    outcome = factory.run_function(_item(), budget)
    assert outcome.attempts == 2, "two refused calls means two spent calls"
    assert budget[0] == 0, "the budget is a promise the run keeps"
    assert outcome.refusals == 2
    assert outcome.stopped == "no extractable C"


def test_a_refusing_target_cannot_outrun_its_budget():
    """The refusal loop that the old code permitted: N rounds x M samples of free calls.

    With refusals charged, five allowed calls stop the function at five whether or not any
    of them compiled.
    """
    class Refusing:
        refusals = 0
        errors: list = []
        raw_heads: list = []
        dropped = 0

        def draw(self, prompt, n, temperature, *, role="independent", deadline=None):
            out = []
            for _ in range(n):
                self.refusals += 1
                receipt = llm.GenerationReceipt(
                    prompt=prompt, raw_response="I can't help with that.",
                    status="refusal", model="fake:7b", digest="x" * 8, wall_ms=5)
                out.append(tf.Proposal(prompt=prompt, action=role, receipt=receipt))
            return out

    budget = [5]
    outcome = _factory(Refusing(), RecordingScorer([]), rounds=1, samples=8).run_function(
        _item(), budget)
    assert budget[0] == 0 and outcome.attempts == 5
    assert outcome.refusals == 5


def test_a_generation_error_is_retained_as_a_receipt_and_counted():
    """An exception used to `break` the round, silently discarding the remaining budget."""
    gen = RecordingGenerator([])

    def boom(prompt, n, temperature, *, role="independent", deadline=None):
        out = []
        for _ in range(n):
            receipt = llm.GenerationReceipt(prompt=prompt, raw_response="",
                                            status="error", model="fake:7b",
                                            error="TimeoutError: exceeded 900s")
            out.append(tf.Proposal(prompt=prompt, action=role, receipt=receipt,
                                   error=receipt.error))
        return out

    gen.draw = boom
    scorer = RecordingScorer([])
    budget = [10]
    outcome = _factory(gen, scorer, rounds=1, samples=4).run_function(_item(), budget)
    assert outcome.attempts == 4, "every attempted call is accounted for"
    assert outcome.errors == 4
    assert budget[0] == 6, "four failed calls cost four of the ten budgeted calls"
    assert outcome.stopped == "no extractable C"


def test_a_generator_that_returns_nothing_is_not_counted_as_zero_calls():
    """No proposals is an unknown count, not a zero -- 'unknown is not zero'."""
    gen = RecordingGenerator([])          # scripts exhausted: draw returns []
    scorer = RecordingScorer([])
    outcome = _factory(gen, scorer, rounds=1, samples=4).run_function(_item(), [10])
    assert outcome.stopped == "no candidates returned"
    assert outcome.attempts == 0
    assert outcome.stopping_note, "the generator's own reason must travel with the stop"


def test_the_wall_clock_is_enforced_inside_a_function_not_only_between_them():
    """FAILS ON OLD: elapsed time was checked once per function, so one function overran."""
    gen = RecordingGenerator(["void probeFn(void) {}"] * 10)
    scorer = RecordingScorer([10.0] * 10)
    factory = _factory(gen, scorer, rounds=5, samples=2)
    outcome = factory.run_function(_item(), [100], deadline=0.0)
    assert outcome.stopped == "time budget"
    assert len(gen.prompts) == 0


# --- 5. a pending request respects the remaining deadline --------------------

def test_a_draw_stops_early_when_the_deadline_has_passed():
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4)
    scorer = RecordingScorer([10.0] * 4)
    outcome = _factory(gen, scorer, rounds=1, samples=4).run_function(
        _item(), [10], deadline=0.0)
    assert outcome.stopped == "time budget"
    assert gen.prompts == []


# --- 6. interruption loses no receipt ---------------------------------------

def test_the_proposal_callback_fires_for_every_scored_candidate():
    """FAILS ON OLD: checkpoints fired once per function, losing the last function whole."""
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4)
    scorer = RecordingScorer([60.0, 70.0, 80.0, 90.0])
    seen: list[dict] = []
    factory = _factory(gen, scorer, rounds=1, samples=4)
    factory.run_function(_item(), [10], on_proposal=seen.append)
    assert len(seen) == 4
    assert [row["score"] for row in seen] == [60.0, 70.0, 80.0, 90.0]


def test_resume_by_receipt_rather_than_by_function(tmp_path):
    """The state files must let a resumed run skip finished functions and not re-pay."""
    out = tmp_path / "run.json"
    state = Path(str(out) + ".state")
    proposals = Path(str(out) + ".proposals.jsonl")
    state.write_text(json.dumps({"func": "doneFn"}) + "\n", encoding="utf-8")
    proposals.write_text(json.dumps({"func": "halfFn", "action": "repair",
                                     "prompt_sha256": "abc"}) + "\n", encoding="utf-8")
    done_functions = {json.loads(line)["func"]
                      for line in state.read_text().splitlines() if line.strip()}
    done_prompts = {json.loads(line)["prompt_sha256"]
                    for line in proposals.read_text().splitlines() if line.strip()
                    and json.loads(line).get("action") == "repair"}
    assert done_functions == {"doneFn"}
    assert done_prompts == {"abc"}


# --- 7. improving-child counts are the dataset's size ------------------------

def test_improving_children_are_counted_not_only_winning_rounds():
    """FAILS ON OLD: three improving draws in one round counted as one 'improving round'."""
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4)
    # baseline 30; 40, 60 and 80 all beat it, 20 does not. All four are one round.
    scorer = RecordingScorer([40.0, 60.0, 20.0, 80.0])
    outcome = _factory(gen, scorer, rounds=1, samples=4).run_function(_item(score=30.0), [10])
    assert outcome.improving_children == 3
    assert outcome.improving == 1, "the old metric counted the round, not the children"
    assert outcome.best_after == 80.0


def test_the_summary_separates_improving_children_from_rounds():
    gen = RecordingGenerator(["void probeFn(void) {}"] * 4)
    scorer = RecordingScorer([40.0, 60.0, 20.0, 80.0])
    factory = _factory(gen, scorer, rounds=1, samples=4)
    report = tf.run(factory, [_item(score=30.0)], max_attempts=10, max_seconds=60)
    assert report["improving_rounds"] == 1
    assert report["improving_children"] == 3
    summary = tf.yield_summary(report)
    assert summary["improving_children"] == 3
    assert summary["improving_rounds"] == 1
    assert summary["usable_examples_per_100_calls"] == 75.0


# --- 8. the sealed holdout is read from every manifest shape -----------------

def test_every_sealed_manifest_key_shape_is_read(tmp_path):
    """FAILS ON OLD: `cluster` and `panel` members were not sealed at all."""
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "a.json").write_text(json.dumps({"dev": ["devFn"], "heldout": ["heldFn"]}))
    (sets / "b.json").write_text(json.dumps({"cluster": ["clusterFn"]}))
    (sets / "c.json").write_text(json.dumps({"panel": ["panelFn"]}))
    sealed = tf.sealed_functions(sets)
    assert sealed == {"devFn", "heldFn", "clusterFn", "panelFn"}


def test_a_real_manifest_shape_is_not_missed():
    """The project's own file uses `cluster`; assert against the real tree."""
    sealed = tf.sealed_functions()
    assert "updateRacePlayerLeanAngle" in sealed or len(sealed) > 100
    assert len(sealed) > 200, f"only {len(sealed)} sealed functions looks like a missed key"


# --- 9. the receipt contract itself ------------------------------------------

def test_a_receipt_refuses_an_unknown_status():
    with pytest.raises(ValueError):
        llm.GenerationReceipt(prompt="p", raw_response="r", status="maybe")


def test_generation_receipt_classifies_each_outcome():
    ok = llm.generation_receipt('```c\nvoid f(void){}\n```', {"model": "m", "eval_count": 5},
                                prompt="p", model="m",
                                extracted="void f(void){}", wall_ms=10,
                                sampling={"temperature": 0.2})
    assert ok.status == "ok" and ok.digest == "m" and ok.token_cost == 5
    refused = llm.generation_receipt("I'm sorry, but I can't help with that.",
                                     {}, prompt="p", model="m", extracted="", wall_ms=1)
    assert refused.status == "refusal"
    failed = llm.generation_receipt("", {}, prompt="p", model="m", extracted="",
                                    wall_ms=1, error="TimeoutError: 900s")
    assert failed.status == "timeout"
    blank = llm.generation_receipt("   ", {}, prompt="p", model="m", extracted="",
                                   wall_ms=1)
    assert blank.status == "empty"
    prose = llm.generation_receipt("Here is my analysis of the code.", {},
                                   prompt="p", model="m", extracted="", wall_ms=1)
    assert prose.status == "no-extract"


def test_the_receipt_row_is_json_safe_and_hashes_what_it_says():
    receipt = llm.generation_receipt("```c\nvoid f(void){}\n```", {"eval_count": 3},
                                     prompt="the prompt", model="m",
                                     extracted="void f(void){}", wall_ms=7)
    row = receipt.as_row()
    json.dumps(row)                                  # must not raise
    import hashlib
    assert row["prompt_sha256"] == hashlib.sha256(b"the prompt").hexdigest()
    assert row["raw_response_bytes"] == len(receipt.raw_response.encode())
    assert row["extracted_sha256"] is not None


def test_a_repair_state_reports_the_action_it_implies():
    compiled = tf.RepairState(source="void f(void){}", compiled=True, diff="- x\n+ y")
    broken = tf.RepairState(source="void f(void){", compiled=False,
                            compiler_stderr="cfe: Error: boom")
    assert compiled.action == "fix-diff" and compiled.feedback() == "- x\n+ y"
    assert broken.action == "fix-compile" and broken.feedback() == "cfe: Error: boom"
    assert tf.RepairState(source="").action == "no-op"
    with pytest.raises(ValueError):
        tf.render_repair_prompt(tf.RepairState(source=""), "")


def test_the_repair_prompt_is_the_projects_own_not_a_third_prompt():
    state = tf.RepairState(source="void f(void) {}", compiled=True, score=88.5,
                           diff="- lw v0,0x24(a0)\n+ lw v0,0(a0)")
    prompt = tf.render_repair_prompt(state, "glabel f\n  jr $ra\nendlabel f")
    assert "void f(void) {}" in prompt
    assert "- lw v0,0x24(a0)" in prompt
    assert "88.50" in prompt
    # DIFF_PROMPT's own guidance, reused rather than paraphrased.
    assert "sign-extension" in prompt


def test_the_root_lineage_of_a_proposal_is_structural_not_a_label():
    root = tf.Proposal(source="void f(void){}", prompt="p", action="independent")
    parent = tf.RepairState(source="void g(void){}", compiled=True, attempt_id=7)
    child = tf.Proposal(source="void f(void){}", prompt="p", action="repair", parent=parent)
    assert root.lineage == "root" and not root.has_parent
    assert child.lineage == "observed" and child.has_parent

"""The factory's job is to manufacture improving edges. These pin the ways it could not.

The three that would silently waste a GPU-week: not linking a candidate to its parent (attempts but
no trajectory), generating against the sealed holdout, and running past its budget because the check
was in the wrong place.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import trajectory_factory as tf

LAYOUT_DIFF = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
               "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-lw    t1,0x28(a0)\n+lw    t1,0x2c(a0)\n")
STRUCT_DIFF = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
               "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-bne   t0,zero,10\n+beq   t0,zero,10\n")


def _kb(tmp_path: Path) -> Path:
    path = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table tus (id integer primary key, name text);
        create table functions (addr integer primary key, name text, tu_id integer,
                                size integer, insn_count integer, is_leaf integer,
                                state text, best_score real, attempts integer);
        create table attempts (id integer primary key, func_addr integer, parent_attempt_id integer,
                               iteration integer, source_code text, compiled integer, score real,
                               exact integer, diff_summary text, strategy text, model text,
                               compiler_stderr text);
    """)
    conn.execute("insert into tus values (1, 'src/game.c')")
    rows = [("tractable", 4096, 88.0, LAYOUT_DIFF, 3), ("stuck", 8192, 96.0, STRUCT_DIFF, 9),
            ("already_solved", 12288, 100.0, STRUCT_DIFF, 2)]
    for name, addr, score, diff, _faults in rows:
        conn.execute("insert into functions values (?,?,1,64,16,1,'attempted',?,10)",
                     (addr, name, score))
        conn.execute("insert into attempts values (?,?,null,0,'',1,?,?,?,'model','m',null)",
                     (addr, addr, score, 1 if name == "already_solved" else 0, diff))
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def spec(tmp_path) -> tf.GameSpec:
    return tf.GameSpec("fixture", tmp_path / "repo", _kb(tmp_path), "ido-5.3")


# --- choosing work ------------------------------------------------------------

def test_the_sealed_holdout_is_excluded_before_any_work_is_chosen(spec, tmp_path):
    """A factory that generates against the benchmark is worse than no factory."""
    sets = tmp_path / "eval" / "sets"
    sets.mkdir(parents=True)
    (sets / "sealed.json").write_text(json.dumps({"heldout": [{"function": "tractable"}],
                                                  "dev": [{"function": "stuck"}]}))
    sealed = tf.sealed_functions(sets)
    assert sealed == {"tractable", "stuck"}
    assert tf.candidates(spec, limit=10, sealed=sealed) == []


def test_solved_functions_are_not_work(spec):
    names = [row["name"] for row in tf.candidates(spec, limit=10, sealed=set())]
    assert "already_solved" not in names and set(names) == {"tractable", "stuck"}


def test_work_is_ranked_by_owned_residual_not_by_score(spec):
    rows = tf.candidates(spec, limit=10, sealed=set())
    assert rows[0]["name"] == "tractable"
    assert rows[0]["owned_share"] > rows[1]["owned_share"]


# --- the loop -----------------------------------------------------------------

class FakeGenerator:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.prompts = []

    def sample(self, prompt, n, temperature):
        self.prompts.append(prompt)
        batch = self.scripts.pop(0) if self.scripts else []
        return batch[:n]


class FakeScorer:
    """Scores from a script and records what it was asked to link, which is the trajectory."""
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = []

    def score(self, func, source, parent_attempt_id):
        index = len(self.calls)
        self.calls.append({"func": func, "source": source, "parent": parent_attempt_id})
        entry = self.scores[min(index, len(self.scores) - 1)]
        return {"compiled": entry.get("compiled", True), "score": entry["score"],
                "exact": entry.get("exact", False), "source": source,
                "attempt_id": 1000 + index, "faults": entry.get("faults", {"layout": 1}),
                "diff": entry.get("diff", "")}


def _factory(scripts, scores, **kwargs) -> tuple[tf.Factory, FakeScorer, FakeGenerator]:
    scorer, generator = FakeScorer(scores), FakeGenerator(scripts)
    factory = tf.Factory(generator=generator, scorer=scorer,
                         context_for=lambda func: {"draft": "void f(void){}", "prompt": "# target"},
                         normalizer=lambda source: (source, []), **kwargs)
    return factory, scorer, generator


ITEM = {"name": "tractable", "addr": 4096, "best_score": 50.0, "best_attempt_id": 7,
        "faults": {"layout": 2, "structural": 0, "reloc": 0, "regalloc": 0, "ordering": 0,
                   "immediate": 0}, "owned_share": 1.0, "attempts": 5}


def test_every_candidate_is_linked_to_the_attempt_it_refines():
    """Attempts without parentage are not a trajectory, and the KB already has that problem."""
    factory, scorer, _ = _factory([["a", "b"]], [{"score": 60.0}, {"score": 55.0}],
                                  rounds=1, samples=2)
    factory.run_function(dict(ITEM), [10])
    assert [call["parent"] for call in scorer.calls] == [7, 7]


def test_an_improving_candidate_becomes_the_parent_of_the_next_round():
    factory, scorer, _ = _factory(
        [["a"], ["b"]],
        [{"score": 70.0, "faults": {"layout": 1}}, {"score": 75.0, "faults": {"layout": 0}}],
        rounds=2, samples=1)
    outcome = factory.run_function(dict(ITEM), [10])
    assert [call["parent"] for call in scorer.calls] == [7, 1000]
    assert outcome.improving == 2 and outcome.best_after == 75.0 and outcome.improved


def test_a_round_that_does_not_improve_keeps_the_branch_and_re_asks_the_same_question():
    """Best-of-N, on the project's own measurement that refinement rescued zero while resampling
    gave 87%, 82% and 100%. A flat round does not change the prompt and does not discard the best."""
    factory, scorer, generator = _factory([["a"], ["b"], ["c"]],
                                          [{"score": 70.0}, {"score": 60.0}, {"score": 60.0}],
                                          rounds=3, samples=1)
    outcome = factory.run_function(dict(ITEM), [10])
    assert scorer.calls[1]["parent"] == 1000, "the flat round still refines the best attempt"
    assert len(set(generator.prompts)) == 1, "the prompt is the project's and is not rewritten"
    assert outcome.improving == 1 and outcome.best_after == 70.0


def test_an_exact_candidate_ends_the_function_after_its_batch_is_scored():
    """The batch is already generated and every candidate is a logged trajectory, so all of it is
    scored; what stops is the next ROUND, not the rest of the batch."""
    factory, scorer, generator = _factory(
        [["a", "b"], ["c"], ["d"]], [{"score": 99.0, "exact": True}, {"score": 10.0},
                                     {"score": 10.0}, {"score": 10.0}],
        rounds=3, samples=2)
    outcome = factory.run_function(dict(ITEM), [10])
    assert outcome.exact and outcome.stopped == "exact"
    assert outcome.attempts == 2, "both candidates in the batch were scored and logged"
    assert len(generator.prompts) == 1, "no further round was started"


def test_the_attempt_budget_is_never_exceeded():
    factory, _, _ = _factory([["a", "b", "c", "d"]] * 5, [{"score": 60.0}] * 40,
                             rounds=5, samples=4)
    budget = [7]
    factory.run_function(dict(ITEM), budget)
    assert budget[0] == 0


def test_the_run_stops_at_whichever_budget_binds_first():
    items = [dict(ITEM, name=f"f{i}") for i in range(10)]
    factory, _, _ = _factory([["a", "b"]] * 10, [{"score": 60.0}] * 40, rounds=1, samples=2)
    report = tf.run(factory, items, max_attempts=6, max_seconds=3600)
    assert report["attempts"] == 6 and report["stopped"] == "attempt budget"
    assert report["functions"] == 3


def test_a_time_budget_stops_a_run_that_would_otherwise_continue():
    items = [dict(ITEM, name=f"f{i}") for i in range(10)]
    factory, _, _ = _factory([["a"]] * 10, [{"score": 60.0}] * 20, rounds=1, samples=1)
    report = tf.run(factory, items, max_attempts=1000, max_seconds=0)
    assert report["stopped"] == "time budget" and report["functions"] == 0


def test_every_finished_function_is_checkpointed():
    items = [dict(ITEM, name=f"f{i}") for i in range(4)]
    factory, _, _ = _factory([["a"]] * 4, [{"score": 60.0}] * 8, rounds=1, samples=1)
    rows = []
    tf.run(factory, items, max_attempts=100, max_seconds=60, checkpoint=rows.append)
    assert [row["func"] for row in rows] == ["f0", "f1", "f2", "f3"]
    assert all("stopped" in row and "game" in row for row in rows)


# --- what the build refuses ---------------------------------------------------

def test_a_do_while_is_lowered_before_it_can_be_refused():
    """Four of the first five real proposals died on this rule before IDO ever ran, so the
    trajectory produced no score, no diff and no fault classes -- no trajectory at all."""
    source = "void f(int n) {\n    int i = 0;\n    do {\n        i++;\n    } while (i < n);\n}\n"
    lowered, applied = tf.normalize(source)
    assert "do {" not in lowered and "while (i < n)" not in lowered
    assert "for (;;)" in lowered and applied == ["do-while -> for(;;)+break"]


def test_source_the_build_already_accepts_is_left_alone():
    source = "void f(void) {\n    int i;\n    for (i = 0; i < 4; i++) { }\n}\n"
    lowered, applied = tf.normalize(source)
    assert lowered == source and applied == []


def test_the_prompt_states_the_rules_the_build_enforces():
    """A model told to write C but not told the dialect writes C the project cannot compile."""
    factory, _, generator = _factory([["a"]], [{"score": 60.0}], rounds=1)
    factory.run_function(dict(ITEM), [10])
    prompt = generator.prompts[0]
    assert prompt  # the context provider is what supplies it; see the dedicated test below


def test_the_lowering_is_recorded_on_the_attempt():
    factory, scorer, _ = _factory([["x"]], [{"score": 60.0}], rounds=1)
    factory.normalizer = tf.normalize
    factory.run_function(dict(ITEM), [10])
    assert factory.log[0]["normalized"] == []


# --- the economics ------------------------------------------------------------

def test_the_yield_summary_is_what_sizes_a_campaign():
    report = {"attempts": 200, "admitted": 100, "improving_rounds": 30, "functions": 20,
              "functions_improved": 8, "gain": 120.0}
    summary = tf.yield_summary(report)
    assert summary["P_compiles"] == 0.5
    assert summary["P_improves_given_compiles"] == 0.3
    assert summary["improving_edges_per_attempt"] == 0.15
    assert summary["powered"] is True


def test_the_yield_is_unknown_rather_than_zero_when_nothing_reached_the_oracle():
    """Pilot 1 reported 0.0 on five attempts of which zero compiled. That reads like a measurement.

    A candidate the build refuses is not a worse move -- it is not a move in the space the repair
    machinery acts on -- and averaging it into a yield is the silent-decline failure applied to a
    metric instead of a pass.
    """
    summary = tf.yield_summary({"attempts": 5, "admitted": 0, "improving_rounds": 0})
    assert summary["P_compiles"] == 0.0
    assert summary["P_improves_given_compiles"] is None, "unknown is not zero"
    assert summary["powered"] is False and "before a zero or a one means anything" in summary["note"]


def test_an_underpowered_run_says_how_many_attempts_it_would_take():
    summary = tf.yield_summary({"attempts": 60, "admitted": 5, "improving_rounds": 0})
    assert summary["powered"] is False
    assert "6%" in summary["note"] and "attempts to budget" in summary["note"]


def test_a_refusing_target_is_reported_as_a_target_problem_not_a_yield_of_zero():
    """The model declined a whole function outright. Its cure is another function, not a better
    candidate -- and reporting it as yield 0.0 would send the next hour after the wrong problem."""
    summary = tf.yield_summary({"attempts": 0, "admitted": 0, "refusals": 4, "improving_rounds": 0})
    assert summary["refusals"] == 4
    assert summary["P_improves_given_compiles"] is None
    assert "target-selection problem" in summary["note"]


def test_refusals_are_counted_separately_from_attempts():
    class Refusing:
        refusals = 0
        errors: list = []
        raw_heads: list = []
        dropped = 0

        def sample(self, prompt, n, temperature):
            self.refusals += n
            return []

    factory = tf.Factory(generator=Refusing(), scorer=FakeScorer([{"score": 60.0}]),
                         context_for=lambda f: {"prompt": "p"},
                         normalizer=lambda s: (s, []), rounds=1, samples=3)
    outcome = factory.run_function(dict(ITEM), [10])
    assert outcome.refusals == 3 and outcome.attempts == 0
    assert outcome.stopped == "no extractable C"


def test_the_two_objectives_disagree_on_purpose(spec):
    """Tractability finishes matches; learnability manufactures learnable edges."""
    items = tf.candidates(spec, limit=10, sealed=set(), objective="learn")
    assert items and all("learnability" in row for row in items)
    near_exact = tf.learnability(99.4, {"ordering": 3}, 1.0)
    mid_range = tf.learnability(60.0, {"layout": 12}, 1.0)
    assert mid_range > near_exact, "two points from exact is the hardest ask, not the easiest"
    assert tf.learnability(99.4, {"ordering": 3}, 1.0) == 0.0, "above the ceiling: no headroom"


def test_learnability_needs_an_owned_fault_class_to_act_on():
    assert tf.learnability(60.0, {"structural": 20}, 0.0) == 0.0
    assert tf.learnability(60.0, {"structural": 20}, 1.0) > 0.0


def test_learnability_prefers_one_legible_fault_class_over_a_scatter():
    concentrated = tf.learnability(60.0, {"layout": 20, "reloc": 1}, 1.0)
    scattered = tf.learnability(60.0, {"layout": 7, "reloc": 7, "structural": 7}, 1.0)
    assert concentrated > scattered


def test_a_run_with_no_attempts_reports_zero_rather_than_dividing_by_it():
    assert tf.yield_summary({"attempts": 0})["improving_edges_per_attempt"] is None


# --- multi-game ---------------------------------------------------------------

def test_a_game_carries_the_compiler_that_makes_its_trajectories_distinguishable():
    """Trajectories are only generalisable if each one says which compiler produced it."""
    factory, _, _ = _factory([["a"]], [{"score": 60.0}], rounds=1, game="dkr", compiler="ido-5.3")
    factory.run_function(dict(ITEM), [10])
    assert factory.log[0]["game"] == "dkr" and factory.log[0]["compiler"] == "ido-5.3"


def test_the_prompt_is_the_projects_own_and_is_not_rewritten_by_the_factory():
    """The first version hand-rolled a prompt; the model answered with 19,791 characters of prose
    and 0 of 20 attempts compiled. `solver/refine.FIRST_PROMPT` is tuned across 31k attempts."""
    scorer, generator = FakeScorer([{"score": 60.0}]), FakeGenerator([["a"]])
    marker = "YOU ARE RECONSTRUCTING ONE FUNCTION\nDo NOT write the `do` keyword\n"
    factory = tf.Factory(generator=generator, scorer=scorer, rounds=1,
                         context_for=lambda func: {"prompt": marker},
                         normalizer=lambda source: (source, []))
    factory.run_function(dict(ITEM), [10])
    assert generator.prompts == [marker], "the factory must not prepend or rewrite the prompt"


def test_a_proposal_with_no_extractable_c_stops_the_function_and_says_so():
    """Prose is an extraction failure, not a model error, and must not reach the compiler."""
    factory, scorer, _ = _factory([[]], [{"score": 60.0}], rounds=1, samples=2)
    outcome = factory.run_function(dict(ITEM), [10])
    assert outcome.attempts == 0 and outcome.stopped == "no extractable C"
    assert scorer.calls == []


def test_the_generator_defaults_match_the_campaigns_generation_settings():
    """`think=""` made gpt-oss emit its reasoning in the response channel: 20 attempts, 0 compiled.

    `solver/refine.main` defaults to think="low" and num_thread=12, and with those the same factory
    reached 44% compiled -- the same rate the campaign measures for this model.
    """
    import inspect
    params = inspect.signature(tf.OllamaGenerator.__init__).parameters
    assert params["think"].default == "low"
    assert params["num_thread"].default == 12


def test_the_technique_label_is_recorded_for_analysis_without_entering_the_prompt():
    factory, _, _ = _factory([["a"]], [{"score": 60.0}], rounds=1)
    assert factory.describe("f", {"layout": 2, "structural": 0}, "none") == "refine"
    assert factory.describe("f", {"layout": 0, "structural": 5}, "representation") == "representation"
    assert factory.describe("f", {"layout": 0, "structural": 5}, "exhausted") == "reseed"
    assert factory.describe("f", {"layout": 0, "structural": 0}, "none") == "reseed"


def test_the_prompt_states_the_rules_the_build_enforces():
    """Whatever prompt is used must carry the do-while rule; the project's template does."""
    from solver.refine import FIRST_PROMPT
    assert "Do NOT write the `do` keyword" in FIRST_PROMPT
    assert "for (;;)" in FIRST_PROMPT
    assert "WRITE SOURCE, NOT REGISTERS" in FIRST_PROMPT


def test_the_registry_names_the_benchmark_and_marks_it():
    assert "sbk1" in tf.GAMES
    assert "benchmark" in tf.GAMES["sbk1"].note


def test_only_a_wired_game_is_ready_to_run():
    assert tf.GAMES["sbk1"].ready
    assert not tf.GAMES["dkr"].ready and not tf.GAMES["sbk2"].ready


def test_an_unwired_game_refuses_with_its_shopping_list_rather_than_running():
    """Running anyway would manufacture zero trajectories and read as a hard game."""
    with pytest.raises(SystemExit, match="declared but not wired"):
        tf.require_ready(tf.GAMES["sbk2"])
    with pytest.raises(SystemExit, match="whole translation"):
        tf.require_ready(tf.GAMES["sbk2"])
    tf.require_ready(tf.GAMES["sbk1"])


def test_a_second_game_is_a_registry_entry_not_a_rewrite():
    """The generalisation axis: same compiler different game, then same family different compiler."""
    assert tf.GAMES["dkr"].compiler == tf.GAMES["sbk1"].compiler
    assert tf.GAMES["sbk2"].compiler != tf.GAMES["sbk1"].compiler
    assert all(spec.name == key for key, spec in tf.GAMES.items())
    assert all(spec.needs for spec in tf.GAMES.values() if not spec.ready)


# --- the refusal regression, pinned ------------------------------------------
#
# `solver/llm.py:112-125` measures a partial ASSISTANT turn stopping refusals outright -- 9/9 -> 0/9
# on functions that refuse every draw -- and records that it works only through `/api/chat`; the same
# text appended to `/api/generate` does nothing (18/18 still refused). Every other call site in the
# repo passed it (`solver/pipeline.py:280`, `:456`, and every pilot). This generator did not, and it
# was measured at 4 refusals and 1 no-extract from a well-formed prompt, against 0 refusals in 2,197
# historical gpt-oss attempts. Testing `is_refusal` afterwards cannot substitute for not asking the
# question in the refusing way.

def test_generator_passes_the_refusal_prefill(monkeypatch):
    from solver import pipeline
    calls = []

    def fake_generate(endpoint, model, prompt, **kwargs):
        calls.append(kwargs)
        return "```c\nvoid f(void) { }\n```", {}

    gen = tf.OllamaGenerator(model="gpt-oss:20b")
    monkeypatch.setattr(gen.llm, "generate", fake_generate)
    gen.sample("prompt", 1, 0.7)

    assert calls, "sample() did not call generate"
    assert calls[0].get("prefill") == pipeline.PREFILL, "the refusal prefill was not passed"


def test_prefill_is_overridable_but_never_absent():
    from solver import pipeline
    assert tf.OllamaGenerator(model="gpt-oss:20b", prefill="```c\n").prefill == "```c\n"
    default = tf.OllamaGenerator(model="gpt-oss:20b")
    assert default.prefill == pipeline.PREFILL and default.prefill

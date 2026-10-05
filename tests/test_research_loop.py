"""The loop must fire on the case it exists for, and must not silently become the greedy policy.

Each test pins one mechanism. The important one is `test_the_controller_takes_the_setback_branch_that
_greedy_abandons`: a forest where the best node sits behind a score-LOWERING edit, which is the
recorded failure this whole module is a response to.
"""
import itertools
import sqlite3

import pytest

from eval import research_loop as rl
from solver import signals

_ids = itertools.count(1000)


def _attempt(score: float, profile: tuple[int, ...], parent: int | None, exact: bool = False,
             func: int = 1) -> rl.Attempt:
    return rl.Attempt(next(_ids), func, parent, 0, float(score), exact, True, None, "test", profile)


def _forest(rows: list[tuple[int, tuple[int, ...]]], links: dict[int, list[int]],
            scores: dict[int, float] | None = None, exact: set[int] | None = None) -> rl.Forest:
    """Build a forest from (id, profile) pairs plus a child map. Ids are given, verdicts are faked."""
    attempts, children, roots = {}, {}, []
    child_ids = {c for kids in links.values() for c in kids}
    for node, profile in rows:
        score = (scores or {}).get(node, 0.0)
        parent = next((p for p, kids in links.items() if node in kids), None)
        attempts[node] = rl.Attempt(node, 1, parent, 0, float(score), node in (exact or set()),
                                    True, None, "test", profile)
        if node not in child_ids:
            roots.append(node)
    for parent, kids in links.items():
        children[parent] = tuple(sorted(kids))
    return rl.Forest(1, attempts, children, tuple(sorted(roots)))


# --- delayed credit -----------------------------------------------------------

def test_a_setbacks_value_is_its_best_descendant_not_its_own_score():
    """The recorded failure: 4,412 score-lowering edits, only 269 ever built on."""
    forest = _forest([(1, (1, 0, 0, 0, 0, 0)), (2, (1, 0, 0, 0, 0, 0)), (3, (0, 1, 0, 0, 0, 0))],
                     {1: [2, 3]}, scores={1: 50.0, 2: 70.0, 3: 40.0})
    assert rl.subtree_value(forest, 2) == (70.0, False)
    assert rl.subtree_value(forest, 3) == (40.0, False)
    # node 3 is worse than node 2 and would be pruned by score. Its subtree decides.
    deeper = _forest([(1, (1, 0, 0, 0, 0, 0)), (2, (1, 0, 0, 0, 0, 0)), (3, (0, 1, 0, 0, 0, 0)),
                      (4, (0, 1, 0, 0, 0, 0))],
                     {1: [2, 3], 3: [4]}, scores={1: 50.0, 2: 70.0, 3: 40.0, 4: 95.0})
    assert rl.subtree_value(deeper, 3) == (95.0, False)
    assert rl.subtree_value(deeper, 2) == (70.0, False)


def test_an_exact_descendant_outranks_a_higher_scored_inexact_one():
    forest = _forest([(1, (1, 0, 0, 0, 0, 0)), (2, (0, 0, 0, 0, 0, 0))], {1: [2]},
                     scores={1: 90.0, 2: 80.0}, exact={2})
    assert rl.subtree_value(forest, 1) == (80.0, True)


# --- plateau typing -----------------------------------------------------------

def _history(profiles, scores):
    return [_attempt(s, p, None) for p, s in zip(profiles, scores)]


def test_a_branch_that_still_improves_is_not_on_a_plateau():
    nodes = _history([(1, 0, 0, 0, 0, 0)] * 5, [10, 20, 30, 40, 50])
    kind, why = rl.classify(nodes)
    assert kind is rl.Plateau.NONE and "improving" in why


def test_the_same_residual_every_time_is_an_exhausted_plateau():
    nodes = _history([(1, 0, 0, 0, 0, 0)] * 5, [50, 50, 50, 50, 50])
    assert rl.classify(nodes)[0] is rl.Plateau.EXHAUSTED


def test_the_same_residual_for_long_enough_is_a_representation_failure():
    """Nine identical residuals mean the edit space, not the edit, is the problem."""
    nodes = _history([(1, 0, 0, 0, 0, 0)] * 9, [50] * 9)
    kind, why = rl.classify(nodes)
    assert kind is rl.Plateau.REPRESENTATION and "invariant" in why


def test_a_flat_score_with_a_moving_residual_is_unresolved_not_exhausted():
    nodes = _history([(5, 0, 0, 0, 0, 0), (0, 5, 0, 0, 0, 0), (0, 0, 5, 0, 0, 0),
                      (0, 0, 0, 5, 0, 0), (0, 0, 0, 0, 5, 0)], [50] * 5)
    kind, why = rl.classify(nodes)
    assert kind is rl.Plateau.UNRESOLVED and "keeps moving" in why


def test_converging_branches_are_detected_across_branches():
    assert rl.converging([(1, 0, 0, 0, 0, 0), (1, 0, 0, 0, 0, 0)])
    assert not rl.converging([(1, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 5)])


# --- the controller -----------------------------------------------------------

def _trap() -> rl.Forest:
    """Root -> (a locally good structural branch) and (a setback with a repairable residual).

    1: structural residual, mediocre. 2,4,5: more of the same, climbing slowly.
    3: LOWER score, but its residual is a layout fault, which diffrepair owns. 6: the payload.
    """
    return _forest(
        [(1, (1, 0, 0, 1, 0, 0)), (2, (1, 0, 0, 1, 0, 0)), (3, (0, 1, 0, 0, 0, 0)),
         (4, (1, 0, 0, 1, 0, 0)), (5, (1, 0, 0, 1, 0, 0)), (6, (0, 1, 0, 0, 0, 0))],
        {1: [2, 3], 2: [4, 5], 3: [6]},
        scores={1: 50.0, 2: 72.0, 3: 40.0, 4: 73.0, 5: 71.0, 6: 96.0})


def _tractability(forest: rl.Forest, node: int, owned: bool) -> None:
    """Install a verdict so the upside term can read `repairable` vs `unrepairable`."""
    verdict = signals.analyse("", 0.0, False, True)
    if owned:
        verdict.layout = 1
    else:
        verdict.structural = 1
    rl._VERDICTS[node] = verdict


def test_the_controller_takes_the_setback_branch_that_greedy_abandons():
    forest = _trap()
    for node in (1, 2, 4, 5):
        _tractability(forest, node, owned=False)
    for node in (3, 6):
        _tractability(forest, node, owned=True)
    greedy = rl.run_episode(forest, rl.Greedy(), budget=3)
    adaptive = rl.run_episode(forest, rl.Adaptive(), budget=3)
    assert not greedy.reached_known_best and greedy.best == 73.0
    assert adaptive.reached_known_best and adaptive.best == 96.0
    assert adaptive.regret == 0.0 and greedy.regret == 23.0


def test_keeping_a_branch_alive_is_not_the_same_as_allocating_to_it():
    """Pareto preserves the setback but still expands the best of the survivors, so it loses here.

    That distinction is the point of a controller: survival without allocation buys nothing.
    """
    forest = _trap()
    for node in (1, 2, 4, 5):
        _tractability(forest, node, owned=False)
    for node in (3, 6):
        _tractability(forest, node, owned=True)
    outcome = rl.run_episode(forest, rl.ParetoPolicy(), budget=3)
    assert not outcome.reached_known_best


def test_no_branch_is_ever_deleted():
    forest = _trap()
    controller = rl.Adaptive()
    policy = rl.Adaptive()
    state = rl.SearchState(forest)
    state.revealed.update(forest.roots)
    seen_counts = []
    for _ in range(8):
        frontier = sorted(state.revealed - state.expanded) or sorted(state.revealed)
        decision = policy.choose(frontier, state)
        kids = forest.children.get(decision.node, ())
        state.expanded.add(decision.node)
        state.revealed.update(kids)
        policy.observe(decision.node, kids, state)
        seen_counts.append(len(policy.branches))
    assert seen_counts == sorted(seen_counts), "branches only ever accumulate"
    assert controller.branches == {}


def test_a_long_flat_run_never_deletes_a_branch():
    policy = rl.Adaptive()
    forest = _forest([(i, (1, 0, 0, 0, 0, 0)) for i in range(1, 12)],
                     {i: [i + 1] for i in range(1, 11)},
                     scores={i: 50.0 for i in range(1, 12)})
    policy.reset(forest)
    state = rl.SearchState(forest)
    state.revealed.add(1)
    counts = []
    for node in range(1, 11):
        kids = forest.children.get(node, ())
        policy.observe(node, kids, state)
        state.expanded.add(node)
        state.revealed.update(kids)
        state.step += 1
        counts.append(len(policy.branches))
    assert policy.plateau is not rl.Plateau.NONE, "a flat run must be diagnosed, not ignored"
    assert counts == sorted(counts), "branches only ever accumulate"
    assert all(branch.nodes for branch in policy.branches.values())


def test_an_exhausted_plateau_raises_exploration():
    """The mechanism: an exhausted plateau shifts the neighbourhood rather than digging harder."""
    policy = rl.Adaptive()
    forest = _trap()
    for node in forest.attempts:
        _tractability(forest, node, owned=True)
    policy.reset(forest)
    state = rl.SearchState(forest)
    state.revealed.update(forest.roots)
    assert policy.floor == policy.base_floor
    policy.plateau = rl.Plateau.EXHAUSTED
    policy.choose(sorted(state.revealed), state)
    assert policy.floor > policy.base_floor
    assert policy.floor <= 0.5, "the floor is bounded, so the policy stays a policy"


def test_a_branch_and_the_tip_it_was_seeded_from_are_not_called_convergent():
    """Otherwise every reseed would be reported as convergence and the diagnosis would be useless."""
    policy = rl.Adaptive()
    forest = _forest([(1, (1, 0, 0, 0, 0, 0)), (2, (1, 0, 0, 0, 0, 0))], {1: [2]},
                     scores={1: 50.0, 2: 50.0})
    policy.reset(forest)
    state = rl.SearchState(forest)
    state.revealed.update(forest.roots)
    policy.observe(1, (2,), state)
    policy.observe(2, (), state)
    assert policy.plateau is not rl.Plateau.CONVERGENCE


def test_the_replay_respects_the_budget():
    forest = _trap()
    for node in forest.attempts:
        _tractability(forest, node, owned=True)
    for policy in (rl.Greedy(), rl.ParetoPolicy(), rl.RandomReseed(), rl.FixedSeed(),
                   rl.Evolutionary(), rl.Adaptive()):
        episode = rl.run_episode(forest, policy, budget=2)
        assert episode.expanded <= 2, policy.name


def test_every_policy_is_deterministic():
    forests = {1: _trap()}
    for node in forests[1].attempts:
        _tractability(forests[1], node, owned=True)
    first = rl.evaluate(forests, [rl.RandomReseed(), rl.Adaptive()], budget=3)
    second = rl.evaluate(forests, [rl.RandomReseed(), rl.Adaptive()], budget=3)
    assert first == second


# --- strategy memory ----------------------------------------------------------

def test_seed_weights_follow_realized_value_and_survive_a_reset():
    book = rl.SeedBook()
    before = book.weights["field-layout"]
    for _ in range(20):
        book.credit("field-layout", 8.0)
        book.credit("symbol-binding", -0.4)
    assert book.weights["field-layout"] > before > book.weights["symbol-binding"]
    policy = rl.Adaptive(seedbook=book)
    policy.reset(None)
    assert policy.seedbook is book, "strategy memory must persist across episodes"
    assert policy.seedbook.weights["field-layout"] > before


def test_a_seed_is_chosen_because_the_residual_points_at_it():
    book = rl.SeedBook()
    assert book.prefer((0, 5, 0, 0, 0, 0)) == "field-layout"
    assert book.prefer((0, 0, 0, 0, 0, 5)) == "literal-value"


def test_the_priority_function_and_the_strategy_memory_are_separable():
    """If the no-learn control matched the learner, the memory would be decoration."""
    forests = {1: _trap()}
    for node in forests[1].attempts:
        _tractability(forests[1], node, owned=True)
    learner, control = rl.Adaptive(), rl.AdaptiveNoLearn()
    assert learner.learn is True and control.learn is False
    run_episode = rl.run_episode(forests[1], learner, budget=3)
    rl.run_episode(forests[1], control, budget=3)
    assert sum(learner.seedbook.uses.values()) > 0
    assert sum(control.seedbook.uses.values()) == 0, "the control must not learn"
    assert run_episode.expanded <= 3


# --- curriculum ---------------------------------------------------------------

def test_weakness_is_measured_from_residuals_of_unsolved_functions():
    forests = {
        1: _forest([(1, (2, 0, 0, 0, 0, 0)), (2, (1, 0, 0, 0, 0, 0))], {1: [2]},
                   scores={1: 50.0, 2: 60.0}),
        2: _forest([(3, (0, 0, 4, 0, 0, 0)), (4, (0, 0, 0, 0, 0, 0))], {3: [4]},
                   scores={3: 40.0, 4: 100.0}, exact={4}),
    }
    weak = rl.weakness(forests)
    assert weak["unsolved"] == 1 and weak["solved"] == 1
    assert weak["dominant"] == "structural"
    assert weak["by_class"]["structural"]["faults"] == 1
    assert weak["by_class"]["reloc"]["faults"] == 0


def test_the_curriculum_names_a_source_for_every_class():
    forests = {1: _forest([(1, (3, 2, 1, 0, 0, 0)), (2, (0, 0, 0, 0, 0, 0))], {1: [2]},
                          scores={1: 40.0, 2: 45.0})}
    plan = rl.curriculum(forests)["plan"]
    assert {row["class"] for row in plan} == set(rl.AXES)
    assert all(row["source"] for row in plan)
    assert all(row["faults"] >= 0 for row in plan)
    # The ordering is the measurement: the largest residual class comes first.
    assert plan[0]["faults"] == max(row["faults"] for row in plan)


def test_register_allocation_is_reported_as_having_no_shape_to_generate_from():
    forests = {1: _forest([(1, (0, 0, 0, 9, 0, 0)), (2, (0, 0, 0, 0, 0, 0))], {1: [2]},
                          scores={1: 40.0, 2: 45.0})}
    row = next(r for r in rl.curriculum(forests)["plan"] if r["class"] == "regalloc")
    assert row["source"] == "none-by-construction" and "no instruction shape" in row["note"]


# --- distillation and the gate ------------------------------------------------

def _trap_pair_forest() -> rl.Forest:
    forest = _trap()
    for node in (1, 2, 4, 5):
        _tractability(forest, node, owned=False)
    for node in (3, 6):
        _tractability(forest, node, owned=True)
    return forest


def test_a_preference_pair_is_labelled_by_what_the_decision_led_to():
    """The setback at node 3 is the BETTER action: its subtree reaches 96, node 2's reaches 73."""
    pairs = rl.distillation_pairs({1: _trap_pair_forest()}, budget=2)
    assert pairs, "the motivating residual must produce at least one pair"
    first = pairs[0]
    assert first["better"]["node"] == 3 and first["better"]["score"] == 40.0
    assert first["better"]["reachable_value"] == 96.0
    assert first["worse"]["node"] == 2 and first["worse"]["reachable_value"] == 73.0
    assert first["value_gap"] == 23.0


def test_the_distilled_state_describes_the_plateau_and_never_the_answer():
    pairs = rl.distillation_pairs({1: _trap_pair_forest()}, budget=2)
    state = pairs[0]["state"]
    assert set(state) == {"score", "faults", "structured", "repairable_share", "expansions",
                          "frontier", "distinct_residuals"}
    assert "source" not in state and "answer" not in state
    assert isinstance(state["structured"], bool)


def test_the_gate_refuses_to_pass_when_a_baseline_matches_the_controller():
    report = {"budget": 16, "policies": {"adaptive": {"P_discovery": 0.80},
                                         "fixed-seed": {"P_discovery": 0.80},
                                         "random": {"P_discovery": 0.50},
                                         "adaptive-nolearn": {"P_discovery": 0.50,
                                                              "solved_functions": []}}}
    verdict = rl.gate(report)
    assert not verdict["passed"] and "fixed-seed" in verdict["reason"]


def _report(adaptive_discovery, fixed, random, *, adaptive_solved, nolearn_solved,
            adaptive_rate=None, nolearn_rate=None) -> dict:
    """A replay report in the shape `evaluate` now writes: rates AND per-function outcomes."""
    return {"budget": 16, "policies": {
        "adaptive": {"P_discovery": adaptive_discovery,
                     "P_solved": adaptive_rate if adaptive_rate is not None
                                 else len(adaptive_solved) / 100,
                     "solved_functions": sorted(adaptive_solved)},
        "fixed-seed": {"P_discovery": fixed},
        "random": {"P_discovery": random},
        "adaptive-nolearn": {"P_discovery": 0.5,
                             "P_solved": nolearn_rate if nolearn_rate is not None
                                         else len(nolearn_solved) / 100,
                             "solved_functions": sorted(nolearn_solved)}}}


def test_the_gate_passes_only_when_the_controller_beats_all_three_controls():
    verdict = rl.gate(_report(0.93, 0.81, 0.82, adaptive_solved={1, 2, 3, 4, 5},
                              nolearn_solved={1, 2}))
    assert verdict["passed"], verdict
    assert "beats fixed seeding" in verdict["reason"]
    assert verdict["conditions"]["R3_beats_no_learn_on_solved"] is True
    assert verdict["paired_solved"] == {"n01": 3, "n10": 0, "both": 2}


def test_the_gate_refuses_when_the_controller_matches_its_no_learn_control():
    """THE MOTIVATING RESIDUAL: the Sept 16 replay, whose real numbers these are.

    `adaptive` and `adaptive-nolearn` scored 0.985 discovery and 0.345 solved -- identical on both
    -- because the priority terms do the work and the learned seed weights never reach a decision.
    Rule version 1 passed this, since it only compared against `fixed-seed` and `random`, neither of
    which has the priority terms. Rule version 2 must refuse it: there is nothing here to distil.
    """
    verdict = rl.gate(_report(0.985, 0.96, 0.9575,
                              adaptive_solved=set(range(138)),
                              nolearn_solved=set(range(138))))
    assert not verdict["passed"], verdict
    assert verdict["conditions"]["R1_beats_fixed_seed_discovery"] is True
    assert verdict["conditions"]["R2_beats_random_discovery"] is True
    assert verdict["conditions"]["R3_beats_no_learn_on_solved"] is False
    assert "no-learn control" in verdict["reason"]
    assert "decoration" in verdict["reason"], "the reason must name the module's own criterion"


def test_the_gate_refuses_a_washed_paired_comparison_that_a_rate_difference_would_hide():
    """Five wins and five losses is a wash, however the rounded rates fall."""
    verdict = rl.gate(_report(0.99, 0.90, 0.90,
                              adaptive_solved={1, 2, 3, 4, 5},
                              nolearn_solved={6, 7, 8, 9, 10},
                              adaptive_rate=0.345, nolearn_rate=0.345))
    assert not verdict["passed"], verdict
    assert verdict["paired_solved"]["n01"] == 5 and verdict["paired_solved"]["n10"] == 5


def test_the_gate_refuses_a_report_that_carries_only_rates():
    """Rates cannot answer a paired question, so their absence is a refusal, not a default pass."""
    verdict = rl.gate({"budget": 16, "policies": {"adaptive": {"P_discovery": 0.99},
                                                  "fixed-seed": {"P_discovery": 0.80},
                                                  "random": {"P_discovery": 0.81},
                                                  "adaptive-nolearn": {"P_discovery": 0.70}}})
    assert not verdict["passed"], verdict
    assert "per-function outcomes" in verdict["reason"]
    assert verdict["conditions"]["R3_beats_no_learn_on_solved"] is False


def test_the_gate_reports_missing_policies_rather_than_passing_by_default():
    verdict = rl.gate({"budget": 8, "policies": {"adaptive": {"P_discovery": 0.99}}})
    assert not verdict["passed"] and "missing policies" in verdict["reason"]


def test_the_gate_names_the_rule_version_it_was_computed_under():
    """Consumers must be able to tell a verdict from the old rule from one under this rule."""
    verdict = rl.gate(_report(0.93, 0.81, 0.82, adaptive_solved={1, 2, 3},
                              nolearn_solved={1}))
    assert verdict["rule_version"] == rl.GATE_RULE_VERSION == 2


# --- observation --------------------------------------------------------------

def test_a_moved_field_access_is_classified_as_layout():
    diff = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
            "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-lw    t1,0x28(a0)\n+lw    t1,0x2c(a0)\n")
    profile = rl.profile_of(diff, 90.0, False, True)
    assert profile[rl.AXES.index("layout")] >= 1


def test_an_inverted_branch_is_classified_structural_not_layout():
    diff = ("--- target_object_dump_normalized.s\n+++ candidate_object_dump_normalized.s\n"
            "@@ -1,3 +1,3 @@\n lw    t0,0x24(a0)\n-bne   t0,zero,10 <.text+0x10>\n"
            "+beq   t0,zero,10 <.text+0x10>\n")
    profile = rl.profile_of(diff, 90.0, False, True)
    assert profile[rl.AXES.index("structural")] >= 1
    assert profile[rl.AXES.index("layout")] == 0


# --- reading the knowledge base -----------------------------------------------

def _memory_kb() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table attempts (id integer primary key, func_addr integer, "
                 "parent_attempt_id integer, iteration integer, score real, exact integer, "
                 "compiled integer, strategy text, model text, diff_summary text)")
    rows = [(1, 10, None, 0, 50.0, 0, 1), (2, 10, 1, 1, 70.0, 0, 1),
            (3, 10, 1, 1, 40.0, 0, 1), (4, 10, 3, 2, 96.0, 0, 1),
            (5, 20, None, 0, 60.0, 0, 1), (6, 20, 5, 1, 61.0, 0, 1),
            (7, 30, None, 0, 10.0, 0, 0)]
    conn.executemany("insert into attempts values (?,?,?,?,?,?,?,null,null,?)",
                     [(*row, "") for row in rows])
    conn.commit()
    return conn


def test_forests_are_per_function_and_roots_are_identified():
    forests = rl.load_forests(_memory_kb(), limit=10, min_attempts=3)
    assert set(forests) == {10}
    forest = forests[10]
    assert forest.roots == (1,)
    assert forest.children[1] == (2, 3)
    assert forest.best_score == 96.0
    assert forest.value(3) == 96.0


def test_the_function_selection_happens_before_any_residual_is_parsed():
    forests = rl.load_forests(_memory_kb(), limit=1, min_attempts=2)
    assert set(forests) == {10}, "the largest forest only"


def test_an_empty_selection_returns_nothing_rather_than_everything():
    assert rl.load_forests(_memory_kb(), limit=10, min_attempts=99) == {}


# --- the live loop ------------------------------------------------------------

class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def sample(self, prompt, n, temperature):
        self.prompts.append(prompt)
        return [f"draft {i}" for i in range(n)]


class FakeScorer:
    """Scores by counting how many drafts it has been shown, so the loop can be observed moving."""
    def __init__(self, scores):
        self.scores = list(scores)
        self.seen = 0

    def score(self, func, source):
        score = self.scores[min(self.seen, len(self.scores) - 1)]
        self.seen += 1
        return True, score, False, ""


def test_the_live_loop_runs_a_step_and_records_what_it_observed():
    forest = _trap()
    for node in forest.attempts:
        _tractability(forest, node, owned=True)
    generator, scorer = FakeGenerator(), FakeScorer([70.0, 40.0, 96.0, 55.0])
    loop = rl.LiveLoop(generator, scorer)
    state = rl.SearchState(forest)
    state.revealed.update(forest.roots)
    record = loop.step(forest, state, n=4, temperature=0.9)
    assert record["func"] == 1
    assert record["action"] in set(rl.LiveLoop.action_for(p) for p in rl.Plateau)
    assert len(record["results"]) == 4
    assert {r["score"] for r in record["results"]} == {70.0, 40.0, 96.0, 55.0}
    assert record["seed"] in rl.SEED_LENSES
    assert loop.log == [record]
    assert "# scale=" in generator.prompts[0]


@pytest.mark.parametrize("plateau,action", [
    (rl.Plateau.NONE, "continue"),
    (rl.Plateau.EXHAUSTED, "reseed-partial"),
    (rl.Plateau.UNRESOLVED, "local-mutation"),
    (rl.Plateau.CONVERGENCE, "reseed-full"),
    (rl.Plateau.REPRESENTATION, "deepen"),
])
def test_each_plateau_selects_its_own_scale_of_move(plateau, action):
    assert rl.LiveLoop.action_for(plateau) == action

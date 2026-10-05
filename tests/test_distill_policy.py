"""The distillation stage must refuse on a failed gate or a leaked holdout, and say what it did.

Training on preference pairs labelled by a controller that is no better than random teaches noise,
and a holdout that overlaps the training functions reports memorisation as improvement. Both are
checked here because neither is recoverable after a training run.
"""
import json

import pytest

from eval import distill_policy as dp
from eval import research_loop as rl

PAIR = {
    "func": 1, "step": 0,
    "state": {"score": 50.0, "faults": {"structural": 1, "layout": 0, "reloc": 0,
                                        "regalloc": 0, "ordering": 0, "immediate": 0},
              "structured": True, "repairable_share": 0.0, "expansions": 1, "frontier": 2,
              "distinct_residuals": 2},
    # Both sides compiled and both carry a residual: this is a choice between two techniques.
    # The higher-value one is a SETBACK by score (40 < 72) whose subtree reaches 96.
    "better": {"node": 3, "score": 40.0, "exact": False,
               "profile": {"structural": 0, "layout": 2, "reloc": 0, "regalloc": 0,
                           "ordering": 0, "immediate": 0},
               "reachable_value": 96.0},
    "worse": {"node": 2, "score": 72.0, "exact": False,
              "profile": {"structural": 3, "layout": 0, "reloc": 0, "regalloc": 0,
                          "ordering": 0, "immediate": 0},
              "reachable_value": 73.0},
    "value_gap": 23.0, "plateau": "none",
}


def _report(adaptive=0.93, fixed=0.81, random=0.82, *, nolearn=0.82,
            adaptive_solved=(), nolearn_solved=()):
    """A replay report in the shape `evaluate` writes: rates AND per-function outcomes.

    The per-function sets are not decoration. The gate's third condition is paired, because the
    question it answers -- does the LEARNED part of the controller contribute anything -- cannot be
    answered from two rounded rates.
    """
    return {"budget": 16, "policies": {
        "adaptive": {"P_discovery": adaptive, "solved_functions": sorted(adaptive_solved)},
        "fixed-seed": {"P_discovery": fixed},
        "random": {"P_discovery": random},
        "adaptive-nolearn": {"P_discovery": nolearn,
                             "solved_functions": sorted(nolearn_solved)}}}


# --- what the model is taught --------------------------------------------------

def test_the_prompt_carries_the_plateau_and_never_the_answer():
    example = dp.build_examples([PAIR])[0]
    assert "plateau=none" in example["prompt"] and "structured=True" in example["prompt"]
    assert "score 50.00" in example["prompt"]
    assert "asset_enums" not in example["prompt"]
    assert set(json.loads(example["chosen"])) == {"kind", "target"}


def test_the_label_is_a_technique_not_a_node_id():
    """`{"node": 3}` indexes one function's tree; `redirect/layout` transfers to another."""
    example = dp.build_examples([PAIR])[0]
    assert example["chosen"] == json.dumps({"kind": "redirect", "target": "layout"}, sort_keys=True)
    assert example["rejected"] == json.dumps({"kind": "refine", "target": "structural"},
                                             sort_keys=True)
    assert "node" not in json.loads(example["chosen"])


def test_refine_means_same_class_as_the_leader_and_redirect_means_a_different_one():
    state = {"faults": {"structural": 5, "layout": 1, "reloc": 0, "regalloc": 0,
                        "ordering": 0, "immediate": 0}}
    same = dp.technique_of({"profile": {"structural": 3}}, state)
    other = dp.technique_of({"profile": {"layout": 3}}, state)
    assert same == {"kind": "refine", "target": "structural"}
    assert other == {"kind": "redirect", "target": "layout"}


def test_a_pair_whose_two_actions_are_the_same_technique_is_dropped():
    state = {"faults": {"structural": 5}, "score": 1.0, "structured": True, "repairable_share": 0.0,
             "expansions": 1, "frontier": 1, "distinct_residuals": 1}
    twin = {"state": state, "plateau": "none", "func": 1, "step": 0, "value_gap": 9.0,
            "better": {"profile": {"structural": 3}}, "worse": {"profile": {"structural": 1}}}
    assert dp.build_examples([twin]) == [], "a coin flip is not a preference"


def test_a_pair_where_the_rejected_side_never_compiled_is_dropped():
    """The bug that produced a fake 1.00: 1,833 of 1,928 pairs had an all-zero rejected profile.

    `signals.analyse` returns an empty profile for a draft that did not compile, so the preference
    being taught was "prefer compiles over non-compiles" -- learnable in twenty steps, and silent.
    """
    never_compiled = dict(PAIR, worse=dict(PAIR["worse"], profile={"structural": 0}))
    assert not dp.informative(never_compiled)
    assert dp.build_examples([never_compiled]) == []


def test_the_majority_baseline_exposes_a_degenerate_dataset():
    """If one constant answer wins, accuracy is measuring the prior and not the task."""
    same = [dict(PAIR) for _ in range(10)]
    assert dp.majority_baseline(dp.build_examples(same)) == 1.0
    flipped = dict(PAIR, better=PAIR["worse"], worse=PAIR["better"])
    mixed = dp.build_examples([PAIR, flipped])
    assert {e["chosen"] for e in mixed} == {'{"kind": "redirect", "target": "layout"}',
                                            '{"kind": "refine", "target": "structural"}'}
    assert dp.majority_baseline(mixed) == 0.5


def test_near_ties_are_dropped_rather_than_trained_on():
    tie = dict(PAIR, value_gap=0.2)
    kept = dp.filter_examples(dp.build_examples([PAIR, tie]), min_gap=1.0)
    assert len(kept) == 1 and kept[0]["value_gap"] == 23.0


# --- the split -----------------------------------------------------------------

def test_the_split_is_by_function_so_no_tree_straddles_it():
    examples = [dict(PAIR, func=f) for f in range(1, 21)]
    train, test = dp.split_by_function(examples, holdout=0.2, seed=7)
    assert train and test
    assert not ({e["func"] for e in train} & {e["func"] for e in test})


def test_the_split_is_deterministic():
    examples = [dict(PAIR, func=f) for f in range(1, 21)]
    assert dp.split_by_function(examples, seed=7) == dp.split_by_function(examples, seed=7)
    a_train, _ = dp.split_by_function(examples, seed=7)
    b_train, _ = dp.split_by_function(examples, seed=8)
    assert {e["func"] for e in a_train} != {e["func"] for e in b_train}


def test_a_split_that_leaves_one_side_empty_is_refused():
    with pytest.raises(dp.Refused, match="split produced"):
        dp.split_by_function([dict(PAIR, func=1)], holdout=0.2)


# --- the two refusals ----------------------------------------------------------

def test_a_failed_gate_is_refused():
    with pytest.raises(dp.Refused, match="does not beat its controls"):
        dp.require_gate(_report(adaptive=0.80, fixed=0.80, random=0.50))


def test_a_passed_gate_is_accepted():
    verdict = dp.require_gate(_report(adaptive_solved={1, 2, 3}, nolearn_solved={1}))
    assert verdict["passed"]


def test_a_controller_that_matches_its_no_learn_control_is_refused():
    """The residue the gate used to pass: nothing to distil, so it must not authorise training."""
    with pytest.raises(dp.Refused, match="no-learn control"):
        dp.require_gate(_report(adaptive_solved={1, 2, 3}, nolearn_solved={1, 2, 3}))


def test_a_stored_verdict_from_the_older_rule_is_recomputed_not_reused():
    """A verdict is only as good as the rule it was computed under.

    The Sept 16 report stores `passed: true` with no rule version -- it was computed by rule 1, which
    never looked at the no-learn control. Reusing it would let a result that passed a weaker rule
    authorise a weight update.
    """
    stored = {"passed": True, "budget": 24, "reason": "beats fixed seeding and random reseeding "
                                                      "on discovery"}
    report = {**_report(adaptive=0.985, fixed=0.96, random=0.9575,
                        adaptive_solved={1, 2, 3}, nolearn_solved={1, 2, 3}),
              "gate": stored}
    with pytest.raises(dp.Refused, match="no-learn control"):
        dp.require_gate(report)


def test_a_stored_verdict_from_the_current_rule_is_reused():
    report = _report(adaptive_solved={1, 2, 3}, nolearn_solved={1})
    report["gate"] = rl.gate(report)
    verdict = dp.require_gate(report)
    assert verdict["rule_version"] == rl.GATE_RULE_VERSION
    assert "recomputed_because" not in verdict


def test_training_on_an_evaluation_function_is_refused():
    with pytest.raises(dp.Refused, match="appear in the training pairs"):
        dp.require_holdout([PAIR], {1, 2, 3})


def test_an_empty_holdout_is_refused_because_the_result_would_be_unmeasurable():
    with pytest.raises(dp.Refused, match="unmeasurable"):
        dp.require_holdout([PAIR], set())


def test_a_disjoint_holdout_is_accepted():
    dp.require_holdout([PAIR], {99, 100})


# --- what it reports -----------------------------------------------------------

def test_the_cli_writes_the_dataset_and_says_training_did_not_run(tmp_path, capsys):
    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text("\n".join(json.dumps(p) for p in [PAIR, dict(PAIR, func=2)]) + "\n")
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(adaptive_solved={1, 2, 3}, nolearn_solved={1})))
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps({"functions": [99]}))
    out = tmp_path / "train.jsonl"
    code = dp.main(["--pairs", str(pairs), "--report", str(report),
                    "--holdout", str(holdout), "--out", str(out)])
    assert code == 0
    assert len(out.read_text().splitlines()) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["examples"] == 2 and summary["training"] == "not run"
    assert "why" in summary, "an honest report says why the weights did not change"


def test_the_cli_refuses_before_writing_a_dataset_when_the_gate_fails(tmp_path):
    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text(json.dumps(PAIR) + "\n")
    report = tmp_path / "report.json"
    # Fails R1/R2 while clearing R3, so the refusal is unambiguously about discovery.
    report.write_text(json.dumps(_report(adaptive=0.5, fixed=0.9, random=0.9,
                                         adaptive_solved={1, 2, 3}, nolearn_solved={1})))
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps({"functions": [99]}))
    out = tmp_path / "train.jsonl"
    with pytest.raises(dp.Refused):
        dp.main(["--pairs", str(pairs), "--report", str(report),
                 "--holdout", str(holdout), "--out", str(out)])
    assert not out.exists(), "no dataset should exist for a distilling run that was refused"


def test_the_stack_report_names_every_module_it_looked_for():
    status = dp.training_stack()
    assert set(status["modules"]) == {"torch", "transformers", "peft", "trl", "datasets"}
    assert isinstance(status["ready"], bool)


# --- the gate that guards all of it -------------------------------------------

def test_the_gate_reads_a_real_replay_report():
    report = {"budget": 4, "policies": {"adaptive": {"P_discovery": 0.985,
                                                     "solved_functions": [1, 2, 3]},
                                        "fixed-seed": {"P_discovery": 0.96},
                                        "random": {"P_discovery": 0.9575},
                                        "adaptive-nolearn": {"P_discovery": 0.975,
                                                             "solved_functions": [1]}}}
    verdict = rl.gate(report, 4)
    assert verdict["passed"] and verdict["budget"] == 4


def test_the_real_sept_16_replay_no_longer_passes_the_gate():
    """The recorded result, with its recorded numbers -- and it must now be refused.

    `eval/results/research-loop-20260916/replay-b24.json` stores `gate.passed: true` and went on to
    write 2,639 distillation pairs. Its own report also records `adaptive` and `adaptive-nolearn` at
    IDENTICAL rates on both metrics (0.985 discovery, 0.345 solved), which under the module's stated
    criterion means "the priority terms are doing the work and the strategy memory is decoration".
    """
    recorded = {"budget": 24, "policies": {
        "adaptive": {"P_discovery": 0.985, "P_solved": 0.345, "solved_functions": list(range(138))},
        "adaptive-nolearn": {"P_discovery": 0.985, "P_solved": 0.345,
                             "solved_functions": list(range(138))},
        "fixed-seed": {"P_discovery": 0.96, "P_solved": 0.3425},
        "random": {"P_discovery": 0.9575, "P_solved": 0.3375}}}
    verdict = rl.gate(recorded, 24)
    assert not verdict["passed"], verdict
    assert verdict["conditions"] == {"R1_beats_fixed_seed_discovery": True,
                                     "R2_beats_random_discovery": True,
                                     "R3_beats_no_learn_on_solved": False}
    assert verdict["paired_solved"] == {"n01": 0, "n10": 0, "both": 138}

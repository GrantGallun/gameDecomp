"""Drive `run_posttraining_experiment.evaluate` with a stub model: the loop, not the weights.

This checks the properties the experiment's honesty rests on, none of which need a GPU:

- the adapter is DISABLED for every M0 draw and ENABLED for every M1 draw;
- both arms get the identical prompt for the identical function;
- the panel is read from the frozen manifest and a tampered panel is refused;
- draws are interleaved per function, so drift cannot land on one arm;
- the preregistered budget is the number of calls actually made.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import run_posttraining_experiment as rpe


class StubModel:
    """Stands in for `LocalModel`. Records the adapter state at every draw."""

    def __init__(self, adapter=True):
        self.adapter_path = "/tmp/fake-adapter" if adapter else ""
        self.adapter_active = False
        self.calls: list[dict] = []

    def load_adapter(self, path):
        self.adapter_path = str(path)
        return {"adapter_path": str(path), "adapter_sha256": "deadbeef"}

    def set_adapter(self, active):
        if active and not self.adapter_path:
            raise RuntimeError("no adapter is loaded; the M1 arm cannot be served")
        self.adapter_active = bool(active)

    def identity(self):
        return {"adapter_active": self.adapter_active, "adapter_path": self.adapter_path}

    def generate(self, prompt, sampler):
        self.calls.append({"adapter_active": self.adapter_active, "prompt": prompt})
        from solver import llm
        text = "```c\nvoid probeFn(void) {}\n```"
        receipt = llm.generation_receipt(
            text, {"model": "stub", "eval_count": 5}, prompt=prompt, model="stub",
            extracted="void probeFn(void) {}",
            sampling={"adapter_active": self.adapter_active}, wall_ms=1)
        return {"text": text, "extracted": "void probeFn(void) {}", "receipt": receipt,
                "input_tokens": 10, "output_tokens": 5, "wall_ms": 1}


class StubScorer:
    """Compiles nothing. Records (arm, prompt, parent) so the contract can be asserted."""

    def __init__(self, arm):
        self.arm = arm
        self.seen: list[dict] = []
        self._next = 1000

    def score(self, func, proposal, parent_attempt_id=None):
        self._next += 1
        self.seen.append({"arm": self.arm, "func": func, "prompt": proposal.prompt,
                          "parent": (proposal.parent.attempt_id
                                     if proposal.parent is not None else None),
                          "action": proposal.action})
        return {"compiled": True, "score": 60.0, "exact": False, "diff": "d",
                "source": proposal.source, "attempt_id": self._next,
                "faults": {}, "status": proposal.status}


def _manifest(tmp_path: Path, panel, *, draws=2, digest=None):
    import hashlib
    panel = list(panel)
    return {
        "panel": panel,
        "panel_sha256": digest or hashlib.sha256(
            json.dumps(panel, sort_keys=True).encode()).hexdigest(),
        "design": {"rounds": 1, "draws_per_function_per_arm": draws, "repair_passes": 0,
                   "sampling": {"temperature": 0.8}},
    }


def _panel(n=2):
    return [{"function": f"fn{i}", "addr": 4096 + i, "size": 64 + i, "tier": "small",
             "insns": 16, "never_attempted": True} for i in range(n)]


def _run(monkeypatch, tmp_path, manifest, arms=("M0", "M1"), draws=2):
    import eval.trajectory_factory as tf
    scorers = {arm: StubScorer(arm) for arm in arms}
    monkeypatch.setattr(tf, "WorkspaceScorer",
                        lambda repo, kb, strategy="", run_id="": scorers[strategy.split("-")[-1]])
    monkeypatch.setattr(tf, "make_context",
                        lambda spec: (lambda func: {"prompt": f"LEAF for {func}",
                                                    "asm": "asm", "hints": ""}))
    monkeypatch.setattr(rpe, "scratch_connection",
                        lambda path, template: SimpleNamespace(execute=lambda *a, **k: None,
                                                               commit=lambda: None))
    model = StubModel()
    summary = rpe.evaluate(manifest, model=model, arms=list(arms), out=tmp_path,
                           repo=tmp_path, kb=tmp_path / "kb.sqlite",
                           sampler=SimpleNamespace(temperature=0.8, top_p=0.95, top_k=0,
                                                   repetition_penalty=1.0,
                                                   max_new_tokens=64, seed=1),
                           only=None, max_seconds=0.0, draws=draws,
                           scratch=tmp_path / "scratch.sqlite")
    return summary, scorers, model


def test_the_adapter_is_off_for_every_m0_draw_and_on_for_every_m1_draw(monkeypatch, tmp_path):
    """The one difference permitted between the arms is the weights.

    Every M0 row must record `adapter_active: false` and every M1 row `true`, AND the
    generator must have been switched to that state before the draw, not after it.
    """
    summary, _scorers, model = _run(monkeypatch, tmp_path, _manifest(tmp_path, _panel(2)))
    for arm in ("M0", "M1"):
        flagged = [row["adapter_active"] for row in summary["arms"][arm]["per_function"]]
        assert flagged, f"{arm} produced no rows"
        assert set(flagged) == {arm == "M1"}, (
            f"{arm} rows disagree about the adapter: {flagged}")
        assert all(row["model_identity"]["adapter_active"] == (arm == "M1")
                   for row in summary["arms"][arm]["per_function"])
    # The generator saw the right state at draw time: M0/M1 alternate per function.
    flags = [call["adapter_active"] for call in model.calls]
    assert flags == [False, False, True, True, False, False, True, True], (
        "draws must be interleaved per function with the adapter toggled for each")


def test_the_arms_are_served_by_the_same_loaded_model_object(monkeypatch, tmp_path):
    """Two arms, one model: a second process is a second chance to differ."""
    summary, _scorers, model = _run(monkeypatch, tmp_path, _manifest(tmp_path, _panel(2)))
    identities = {arm: {row["model_identity"]["adapter_path"]
                        for row in summary["arms"][arm]["per_function"]}
                  for arm in ("M0", "M1")}
    assert identities["M0"] == identities["M1"] == {model.adapter_path}


def test_both_arms_receive_the_identical_prompt_for_a_function(monkeypatch, tmp_path):
    summary, scorers, _ = _run(monkeypatch, tmp_path, _manifest(tmp_path, _panel(2)))
    by_arm = {arm: {row["func"]: row["prompt"] for row in rows.seen}
              for arm, rows in scorers.items()}
    assert by_arm["M0"] == by_arm["M1"], "the arms must differ only in weights"


def test_the_preregistered_draw_count_is_the_number_of_calls_made(monkeypatch, tmp_path):
    summary, scorers, model = _run(monkeypatch, tmp_path, _manifest(tmp_path, _panel(3)),
                                   draws=2)
    for arm in ("M0", "M1"):
        assert summary["arms"][arm]["model_calls"] == 6, "3 functions x 2 draws"
    assert len(model.calls) == 12


def test_a_tampered_panel_is_refused(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, _panel(2), digest="0" * 64)
    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, tmp_path, manifest)
    assert "preregistered" in str(excinfo.value)


def test_the_comparison_reports_a_paired_difference_not_a_marginal(monkeypatch, tmp_path):
    """A gain of one function by M1 must be named, and a tie must read inconclusive."""
    from eval.equal_budget_eval import compare
    m0 = {"per_function": [{"function": "a", "exact": False},
                           {"function": "b", "exact": True},
                           {"function": "c", "exact": False}]}
    m1 = {"per_function": [{"function": "a", "exact": True},
                           {"function": "b", "exact": True},
                           {"function": "c", "exact": False}]}
    out = compare(m0, m1)
    assert out["gained_by_m1"] == ["a"] and out["gained_by_m0"] == []
    assert out["paired_difference"] == 1 and out["verdict"] == "M1"
    assert out["exact_m0"] == 1 and out["exact_m1"] == 2

    tie = compare(m0, {"per_function": [{"function": "a", "exact": False},
                                        {"function": "b", "exact": True},
                                        {"function": "c", "exact": False}]})
    assert tie["verdict"].startswith("inconclusive"), "a tie is not a success"


def test_every_draw_in_a_round_gets_a_different_seed():
    """A shared seed is not best-of-N, it is best-of-1 reported N times.

    Measured on the 2026-09-20 collection pilot: the generator passed one fixed `seed` for
    every request, and the service treats an explicit seed as FIXED-SEED sampling. 68
    "independent" draws were 17 distinct generations repeated four times each, and all 12
    repair draws collapsed onto a single output per function -- one of which was the candidate
    byte-for-byte. The reported 0/12 repair yield was entirely a seeding artifact.
    """
    from eval.inference_generator import derive_seed

    seeds = [derive_seed(7000, "someFn", "independent", i) for i in range(4)]
    assert len(set(seeds)) == 4, f"draws must not share a seed: {seeds}"
    assert all(seed is not None for seed in seeds)
    # Reproducible: the same (function, role, index) gives the same seed every time.
    assert derive_seed(7000, "someFn", "independent", 2) == seeds[2]
    # Different functions, roles and indices do not collide.
    assert derive_seed(7000, "otherFn", "independent", 0) != seeds[0]
    assert derive_seed(7000, "someFn", "repair", 0) != seeds[0]
    assert derive_seed(7001, "someFn", "independent", 0) != seeds[0]
    # An unseeded run stays unseeded.
    assert derive_seed(None, "someFn", "independent", 0) is None


def test_the_generator_is_told_the_function_before_its_draws():
    """The seed basis includes the function, so the generator must be given it."""
    from eval.inference_generator import ServeGenerator

    generator = ServeGenerator.__new__(ServeGenerator)
    generator.function = ""
    generator.begin_function("probeFn")
    assert generator.function == "probeFn"

    from eval.local_model import InProcessGenerator
    other = InProcessGenerator.__new__(InProcessGenerator)
    other.function = ""
    other.begin_function("probeFn")
    assert other.function == "probeFn"


def test_the_factory_tells_the_generator_which_function_it_is_working_on(monkeypatch, tmp_path):
    """The generator is what derives the seed, so the generator is what must be told.

    Asserting on the model instead would pass while the wrapper never forwarded the name.
    """
    from eval import local_model as lm

    seen: list[str] = []

    class Noticing(lm.InProcessGenerator):
        def begin_function(self, name):
            seen.append(name)
            super().begin_function(name)

    import eval.trajectory_factory as tf
    monkeypatch.setattr(tf, "WorkspaceScorer",
                        lambda repo, kb, strategy="", run_id="": StubScorer("M0"))
    monkeypatch.setattr(tf, "make_context",
                        lambda spec: (lambda func: {"prompt": "p", "asm": "a", "hints": ""}))
    monkeypatch.setattr(rpe, "scratch_connection",
                        lambda path, template: SimpleNamespace(execute=lambda *a, **k: None,
                                                               commit=lambda: None))
    monkeypatch.setattr(lm, "InProcessGenerator", Noticing)
    rpe.evaluate(_manifest(tmp_path, _panel(2)), model=StubModel(), arms=["M0"],
                 out=tmp_path, repo=tmp_path, kb=tmp_path / "k.sqlite",
                 sampler=SimpleNamespace(temperature=0.8, top_p=0.95, top_k=0,
                                         repetition_penalty=1.0, max_new_tokens=8, seed=1),
                 only=None, max_seconds=0.0, draws=1, scratch=tmp_path / "s.sqlite")
    assert seen == ["fn0", "fn1"], "one announcement per function, before its draws"


def test_a_panel_member_that_cannot_bootstrap_is_recorded_not_fatal(monkeypatch, tmp_path):
    """One unbootstrappable function must not discard the rest of the panel.

    Measured 2026-09-20: `gspF3DLX_fifoStart` is SDK macro assembly, so `bootstrap` raises
    `AssemblyBackendRequired`, and a bare exception ended a 60-function run at function 57 --
    throwing away 56 functions of completed work and reporting nothing at all.
    """
    import eval.trajectory_factory as tf

    real_make_context = tf.make_context

    def exploding_context(spec):
        def context_for(func):
            if func == "fn1":
                raise RuntimeError("AssemblyBackendRequired: SDK macro assembly")
            return {"prompt": "p", "asm": "a", "hints": ""}
        return context_for

    monkeypatch.setattr(tf, "WorkspaceScorer",
                        lambda repo, kb, strategy="", run_id="": StubScorer("M0"))
    monkeypatch.setattr(tf, "make_context", exploding_context)
    monkeypatch.setattr(rpe, "scratch_connection",
                        lambda path, template: SimpleNamespace(execute=lambda *a, **k: None,
                                                               commit=lambda: None))
    summary = rpe.evaluate(_manifest(tmp_path, _panel(3)), model=StubModel(), arms=["M0"],
                           out=tmp_path, repo=tmp_path, kb=tmp_path / "k.sqlite",
                           sampler=SimpleNamespace(temperature=0.8, top_p=0.95, top_k=0,
                                                   repetition_penalty=1.0, max_new_tokens=8,
                                                   seed=1),
                           only=None, max_seconds=0.0, draws=1, scratch=tmp_path / "s.sqlite")
    arm = summary["arms"]["M0"]
    assert arm["functions_run"] == 3, "the panel continued past the bad member"
    assert arm["functions_ineligible"] == 1
    rows = {row["function"]: row for row in arm["per_function"]}
    assert rows["fn1"]["stopped"] == "panel member ineligible"
    assert "AssemblyBackendRequired" in rows["fn1"]["ineligible_reason"]
    assert rows["fn1"]["best_score"] is None, "ineligible is not a score of zero"
    # The other two were still measured.
    assert rows["fn0"]["draws"] == 1 and rows["fn2"]["draws"] == 1
    # An ineligible member does not drag the mean toward zero.
    assert arm["mean_best_score"] == 60.0


def test_a_missing_adapter_cannot_serve_the_m1_arm(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path, _panel(1))

    class NoAdapter(StubModel):
        def __init__(self):
            super().__init__(adapter=False)

    import eval.trajectory_factory as tf
    monkeypatch.setattr(tf, "make_context",
                        lambda spec: (lambda func: {"prompt": "p", "asm": "a", "hints": ""}))
    monkeypatch.setattr(rpe, "scratch_connection",
                        lambda path, template: SimpleNamespace(execute=lambda *a, **k: None,
                                                               commit=lambda: None))
    with pytest.raises(RuntimeError):
        rpe.evaluate(manifest, model=NoAdapter(), arms=["M1"], out=tmp_path, repo=tmp_path,
                     kb=tmp_path / "k.sqlite",
                     sampler=SimpleNamespace(temperature=0.8, top_p=0.95, top_k=0,
                                             repetition_penalty=1.0, max_new_tokens=8,
                                             seed=1),
                     only=None, max_seconds=0.0, draws=1, scratch=tmp_path / "s.sqlite")

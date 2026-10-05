"""Resource caps must be real, adjustable, and visible in the run record.

The failure these pin: a training run and an inference server were started with no per-process
limits, which on this box means all four vCPUs and a CUDA allocator that grows until VRAM is
gone. The machine stays usable only by accident, and a later comparison cannot tell a resource
limit apart from a capability limit unless the limit is written down.
"""
from __future__ import annotations

import os

import pytest

from eval import resource_limits as rl


def test_the_defaults_leave_more_than_half_the_machine_free():
    cfg = rl.limits()
    assert cfg.gpu_memory_fraction <= 0.5, "default must not reserve most of the card"
    assert cfg.cpu_threads <= 2, "default must not take every core"
    assert cfg.nice >= 10, "default must yield CPU to interactive work"
    assert cfg.vllm_gpu_utilization <= 0.5
    assert cfg.vllm_max_seqs <= 2


def test_every_knob_is_an_environment_variable(monkeypatch):
    monkeypatch.setenv("SOLVER_GPU_MEMORY_FRACTION", "0.25")
    monkeypatch.setenv("SOLVER_CPU_THREADS", "1")
    monkeypatch.setenv("SOLVER_NICE", "19")
    monkeypatch.setenv("SOLVER_VLLM_GPU_UTILIZATION", "0.2")
    monkeypatch.setenv("SOLVER_VLLM_MAX_SEQS", "1")
    monkeypatch.setenv("SOLVER_GPU_MEMORY_GB", "4.0")
    cfg = rl.limits()
    assert cfg.gpu_memory_fraction == 0.25
    assert cfg.cpu_threads == 1
    assert cfg.nice == 19
    assert cfg.vllm_gpu_utilization == 0.2
    assert cfg.vllm_max_seqs == 1
    assert cfg.gpu_memory_gb == 4.0
    # GB wins over the fraction when both are set.
    assert rl.torch_device_limit_gb() == 4.0


def test_a_malformed_value_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("SOLVER_CPU_THREADS", "two")
    monkeypatch.setenv("SOLVER_GPU_MEMORY_FRACTION", "")
    cfg = rl.limits()
    assert cfg.cpu_threads == rl.DEFAULTS["cpu_threads"]
    assert cfg.gpu_memory_fraction == rl.DEFAULTS["gpu_memory_fraction"]


def test_apply_caps_the_thread_pool_and_reports_what_it_did(monkeypatch):
    monkeypatch.setenv("SOLVER_CPU_THREADS", "1")
    monkeypatch.setenv("SOLVER_NICE", "19")
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        monkeypatch.delenv(name, raising=False)
    cfg = rl.apply(cuda=False)
    assert cfg.applied
    assert cfg.cpu_threads == 1
    assert os.environ["OMP_NUM_THREADS"] == "1"
    assert os.environ["MKL_NUM_THREADS"] == "1"
    described = rl.describe()
    assert described["requested"]["cpu_threads"] == 1
    assert described["env"]["OMP_NUM_THREADS"] == "1"


def test_apply_never_raises_when_there_is_no_gpu(monkeypatch):
    """A resource limit must not become a new reason a run fails."""
    monkeypatch.setenv("SOLVER_GPU_MEMORY_GB", "1.0")
    cfg = rl.apply(cuda=True)          # no CUDA in the test environment
    assert cfg.applied is True
    assert isinstance(cfg.device_total_gb, float)


def test_the_serve_script_uses_the_capped_values():
    """The shell entry point must read the same environment variables.

    Asserted on the DEFAULT assignments, not on the whole file: the script's comment documents
    how to widen the reservation, and a substring check over the file would fail on its own
    documentation.
    """
    script = (rl.__file__.replace("eval/resource_limits.py", "")
              + ".cache/recon/serve_lowfootprint.sh")
    text = open(script, encoding="utf-8").read()
    defaults = [line for line in text.splitlines()
                if ":-" in line and "SOLVER_" in line]
    assert defaults, "the script must read its limits from the environment with defaults"
    joined = "\n".join(defaults)
    assert "SOLVER_VLLM_GPU_UTILIZATION:-0.42" in joined
    assert "SOLVER_VLLM_MAX_SEQS:-2" in joined
    assert "SOLVER_NICE:-15" in joined
    assert "nice -n" in text, "the server must run at low CPU priority"
    assert "0.72" not in joined, "the default must not be the old 72% reservation"


# --- yielding the machine back ------------------------------------------------

def test_pause_is_a_file_and_is_reported_with_its_path(monkeypatch, tmp_path):
    marker = tmp_path / "PAUSE"
    monkeypatch.setenv(rl.PAUSE_ENV, str(marker))
    assert rl.paused() is False
    marker.write_text("stop please")
    assert rl.paused() is True
    note = rl.wait_note()
    assert str(marker) in note
    assert "resume" in note.lower(), "the note must say how to continue"


def test_a_run_stops_between_rounds_when_paused():
    """Stopping must be safe: the work already done is scored and stored, not discarded."""
    from eval import trajectory_factory as tf

    class Counting:
        def __init__(self):
            self.prompts = 0
            self.refusals = 0
            self.errors: list = []
            self.raw_heads: list = []
            self.dropped = 0

        def sample(self, prompt, n, temperature):
            self.prompts += 1
            return ["void f(void) {}"] * n

    class Scorer:
        def __init__(self):
            self.seen = 0

        def score(self, func, proposal, parent_attempt_id=None):
            self.seen += 1
            return {"compiled": True, "score": 50.0, "exact": False, "diff": "d",
                    "source": "void f(void) {}", "attempt_id": self.seen, "faults": {}}

    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1          # let round 0 run, stop before round 1

    generator, scorer = Counting(), Scorer()
    factory = tf.Factory(generator=generator, scorer=scorer,
                         context_for=lambda f: {"prompt": "p", "asm": "a"},
                         normalizer=lambda s: (s, []), rounds=3, samples=2,
                         should_stop=staticmethod(should_stop),
                         pause_note=staticmethod(lambda: "paused by test"))
    outcome = factory.run_function({"name": "fn", "best_score": 0.0}, [10])
    assert outcome.stopped == "paused"
    assert outcome.attempts == 2, "the round already in flight was completed and stored"
    assert outcome.stopping_note == "paused by test"
    assert scorer.seen == 2
    assert generator.prompts == 1, "no further round was started"

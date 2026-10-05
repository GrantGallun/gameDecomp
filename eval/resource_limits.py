"""Every knob that decides how much of the machine a run may take, in one place.

WHY THIS EXISTS
---------------
A training run and an inference server were started with no per-process limits, and on this
box that means: all four vCPUs (the WSL cap), a PyTorch allocator that grows to fill VRAM, and
a vLLM server that reserves 72% of the card whether or not it is serving anything. The machine
stays usable only by accident.

The defaults below deliberately leave the larger share for whoever is using the computer:

    GPU            50% of the card, 8.0 GB of 15.9 GB
    vLLM server    42% reservation, 6.5 GB
    CPU threads    2 of 4
    process nice   15 (low priority, so an interactive job wins every contest)

Every value is an environment variable, so a run can be widened when the machine is free or
narrowed further without editing code:

    SOLVER_GPU_MEMORY_FRACTION   torch allocator cap as a fraction of total VRAM
    SOLVER_GPU_MEMORY_GB         the same cap, stated in GB (wins over the fraction)
    SOLVER_CPU_THREADS           torch / OpenMP / MKL thread count
    SOLVER_NICE                  niceness for the whole run (0..19)
    SOLVER_VLLM_GPU_UTILIZATION  vLLM's `--gpu-memory-utilization`
    SOLVER_VLLM_MAX_SEQS         vLLM's `--max-num-seqs`

Call `apply()` before the first CUDA allocation. `describe()` returns what was applied, to be
written into the run report so a resource limit is never mistaken for a capability limit.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

DEFAULTS = {
    "gpu_memory_fraction": 0.50,
    "cpu_threads": 2,
    "nice": 15,
    "vllm_gpu_utilization": 0.42,
    "vllm_max_seqs": 2,
}


@dataclass
class Limits:
    gpu_memory_fraction: float
    gpu_memory_gb: float
    cpu_threads: int
    nice: int
    vllm_gpu_utilization: float
    vllm_max_seqs: int
    applied: bool = False
    device_total_gb: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def limits() -> Limits:
    return Limits(
        gpu_memory_fraction=_env_float("SOLVER_GPU_MEMORY_FRACTION",
                                       DEFAULTS["gpu_memory_fraction"]),
        gpu_memory_gb=_env_float("SOLVER_GPU_MEMORY_GB", 0.0),
        cpu_threads=max(1, _env_int("SOLVER_CPU_THREADS", DEFAULTS["cpu_threads"])),
        nice=max(0, min(19, _env_int("SOLVER_NICE", DEFAULTS["nice"]))),
        vllm_gpu_utilization=_env_float("SOLVER_VLLM_GPU_UTILIZATION",
                                        DEFAULTS["vllm_gpu_utilization"]),
        vllm_max_seqs=max(1, _env_int("SOLVER_VLLM_MAX_SEQS", DEFAULTS["vllm_max_seqs"])),
    )


def apply(*, cuda: bool = True) -> Limits:
    """Cap CPU threads, raise niceness, and cap the CUDA allocator. Idempotent.

    Thread limits are set BEFORE torch is imported by the caller's process -- setting
    `torch.set_num_threads` afterwards does not stop OpenMP from having already sized its pool,
    so the environment variables are the ones that matter.
    """
    cfg = limits()
    os.environ.setdefault("OMP_NUM_THREADS", str(cfg.cpu_threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(cfg.cpu_threads))
    os.environ.setdefault("OPENBLAS_NUM_THREADS", str(cfg.cpu_threads))
    os.environ.setdefault("NUMEXPR_NUM_THREADS", str(cfg.cpu_threads))
    os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")
    os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
    # Expandable segments avoids the allocator hoarding freed blocks, which is what makes a
    # "capped" process still push the card to zero free.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    try:
        os.nice(cfg.nice)
    except (OSError, AttributeError):
        # `os.nice` does not exist on Windows, and an AttributeError is not an OSError, so this
        # `try` used to raise for ANY native-Windows caller of `apply()` -- every harness that merely
        # wanted its caps set died at the politeness line. Niceness is an optimisation, never a
        # precondition.
        pass
    if cuda:
        try:
            import torch
            torch.set_num_threads(cfg.cpu_threads)
            if torch.cuda.is_available():
                total = torch.cuda.get_device_properties(0).total_memory
                cfg.device_total_gb = round(total / (1024 ** 3), 2)
                want_bytes = (cfg.gpu_memory_gb * (1024 ** 3) if cfg.gpu_memory_gb > 0
                              else total * cfg.gpu_memory_fraction)
                torch.cuda.set_per_process_memory_fraction(
                    min(1.0, want_bytes / total), 0)
        except Exception as exc:                      # a limit must never break a run
            cfg.device_total_gb = -1.0
            os.environ["SOLVER_LIMIT_ERROR"] = f"{type(exc).__name__}: {exc}"
    cfg.applied = True
    return cfg


def describe() -> dict:
    cfg = limits()
    return {"requested": cfg.as_dict(),
            "env": {k: os.environ.get(k, "") for k in
                    ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "PYTORCH_CUDA_ALLOC_CONF")}}


def torch_device_limit_gb() -> float:
    """The VRAM budget in GB, for a report or a server flag."""
    cfg = limits()
    if cfg.gpu_memory_gb > 0:
        return cfg.gpu_memory_gb
    return round(15.9 * cfg.gpu_memory_fraction, 2)


# --- yielding the machine back on request -------------------------------------

PAUSE_ENV = "SOLVER_PAUSE_FILE"
DEFAULT_PAUSE_FILE = "/home/grant/decomp/posttraining-m1-20260920/PAUSE"


def pause_file() -> "os.PathLike[str] | str":
    return os.environ.get(PAUSE_ENV) or DEFAULT_PAUSE_FILE


def paused() -> bool:
    """True when a human has asked running jobs to stop.

    A long unattended run and a person using the same computer cannot negotiate. `touch` on the
    pause file is that negotiation, and it works because it is checked at the only place that
    matters -- between model calls, where stopping costs at most the current candidate.

    Stopping this way is SAFE rather than merely polite: every attempt has already been scored
    and stored, the state sidecar records finished functions, and a resumed run skips them. The
    checkpointing that makes the experiment resumable is the same machinery that makes it
    interruptible, so this adds no new failure mode.
    """
    try:
        return os.path.exists(pause_file())
    except OSError:
        return False


def wait_note() -> str:
    return (f"paused: {pause_file()} exists. Remove it to resume; every completed attempt is "
            f"already stored and a resumed run skips finished functions.")

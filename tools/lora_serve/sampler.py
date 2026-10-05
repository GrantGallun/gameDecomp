"""Per-row sampling with per-request seeds.

Why this exists
---------------
Two things the repo's experiment design needs are not expressible in
transformers' built-in sampler when requests are batched:

1. **Per-row parameters.** A batch mixes requests with different temperature,
   top_p, top_k or penalties. Transformers' warpers take scalars for the whole
   batch (in transformers 5 the warpers are folded into the logits-processor
   list, one instance per batch, not per row).
2. **Per-row seeds.** ``torch.multinomial`` draws from the global RNG, so
   "seed 7 gives this exact sample" cannot be promised per request inside a
   batch.

The mechanism used here is a single :class:`~transformers.LogitsProcessor` that
(a) applies every penalty/warp itself, per row, (b) draws one token per row from a
per-row ``torch.Generator``, and (c) returns logits that FORCE that token by
putting the whole probability mass on it. Transformers' own sampler then draws
from a degenerate distribution, so the token is not a function of the global RNG.

What this does and does not guarantee (measured, see the results README):

* Same (prompt, params, seed) -> identical text, including across server
  restarts. Verified.
* Same seed in a *differently composed batch* can differ in the last bits of the
  logits (batched matmul reduction order), which can flip a near-tie. The
  guarantee is therefore "reproducible for identical batch composition", which
  is what a receipt needs; it is not a promise of bitwise-identical logits.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

import torch
from transformers import LogitsProcessor

__all__ = ["RowSampling", "PerRowSampler", "StepTiming"]

_NEG = -1.0e4


@dataclass(frozen=True)
class RowSampling:
    """Validated sampling parameters for one sequence."""

    temperature: float | None = 0.2
    top_p: float | None = 0.8
    top_k: int | None = 20
    repetition_penalty: float | None = 1.1
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repetition_penalty": self.repetition_penalty,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "seed": self.seed,
        }


@dataclass
class StepTiming:
    """Where the wall clock went inside one batched generation."""

    started: float = 0.0
    first_step: float | None = None
    last_step: float | None = None
    steps: int = 0

    @property
    def prefill_ms(self) -> float:
        if self.first_step is None:
            return 0.0
        return (self.first_step - self.started) * 1000.0

    @property
    def decode_ms(self) -> float:
        if self.first_step is None or self.last_step is None:
            return 0.0
        return (self.last_step - self.first_step) * 1000.0


class PerRowSampler(LogitsProcessor):
    """Sample one token per row with per-row parameters, seeds and penalties."""

    def __init__(self, rows: Sequence[RowSampling], *, eos_token_ids: Sequence[int] = (),
                 device: str | torch.device = "cpu", timing: StepTiming | None = None):
        self.rows = list(rows)
        self.eos_token_ids = tuple(int(t) for t in eos_token_ids)
        self.timing = timing if timing is not None else StepTiming()
        self.timing.started = self.timing.started or time.monotonic()
        self.generators: list[torch.Generator] = []
        for row in self.rows:
            generator = torch.Generator(device=torch.device(device))
            if row.seed is None:
                generator.seed()
            else:
                generator.manual_seed(int(row.seed) & 0x7FFF_FFFF_FFFF_FFFF)
            self.generators.append(generator)
        self.chosen: list[int] = []
        self.greedy_flags: list[bool] = [
            bool(row.temperature is None or float(row.temperature) <= 0.0)
            for row in self.rows]

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _top_k_filter(logits: torch.Tensor, k: int) -> None:
        if k <= 0 or k >= logits.numel():
            return
        threshold = torch.topk(logits, k, dim=-1).values[..., -1, None]
        logits[logits < threshold] = _NEG

    @staticmethod
    def _top_p_filter(logits: torch.Tensor, p: float) -> None:
        if not 0.0 < p < 1.0:
            return
        sorted_logits, sorted_index = torch.sort(logits, descending=True, dim=-1)
        cumulative = torch.softmax(sorted_logits, dim=-1).cumsum(dim=-1)
        # Keep the smallest prefix of the sorted distribution whose cumulative
        # probability first reaches p (the token that crosses p is kept).
        remove = cumulative - torch.softmax(sorted_logits, dim=-1) >= p
        sorted_logits = sorted_logits.masked_fill(remove, _NEG)
        logits.copy_(sorted_logits.scatter(-1, sorted_index, sorted_logits))

    @torch.no_grad()
    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor
                 ) -> torch.FloatTensor:
        now = time.monotonic()
        if self.timing.first_step is None:
            self.timing.first_step = now
        self.timing.last_step = now
        self.timing.steps += 1

        batch, vocab = scores.shape
        if batch != len(self.rows):
            raise RuntimeError(
                f"sampler configured for {len(self.rows)} rows but got a batch of {batch}")
        device = scores.device
        work = scores.float()
        chosen = torch.empty(batch, dtype=torch.long, device=device)

        for index, row in enumerate(self.rows):
            logits = work[index]
            seen = input_ids[index]

            if row.repetition_penalty and row.repetition_penalty != 1.0:
                unique = torch.unique(seen)
                values = logits[unique]
                penalty = float(row.repetition_penalty)
                logits[unique] = torch.where(values < 0, values * penalty, values / penalty)
            if row.frequency_penalty or row.presence_penalty:
                counts = torch.bincount(seen, minlength=vocab).to(logits.dtype)
                if row.frequency_penalty:
                    logits -= float(row.frequency_penalty) * counts
                if row.presence_penalty:
                    logits -= float(row.presence_penalty) * (counts > 0).to(logits.dtype)

            greedy = self.greedy_flags[index]
            if not greedy:
                temperature = float(row.temperature)
                if temperature != 1.0:
                    logits /= temperature
                if row.top_k:
                    self._top_k_filter(logits, int(row.top_k))
                if row.top_p is not None:
                    self._top_p_filter(logits, float(row.top_p))
                probs = torch.softmax(logits, dim=-1)
                if not torch.isfinite(probs).all() or float(probs.sum()) <= 0.0:
                    # Degenerate distribution: never silently emit garbage.
                    token = int(torch.argmax(scores[index]).item())
                else:
                    token = int(torch.multinomial(
                        probs, num_samples=1, generator=self.generators[index]).item())
            else:
                token = int(torch.argmax(scores[index]).item())
            chosen[index] = token

        self.chosen = [int(t) for t in chosen.tolist()]

        # Force the drawn token: transformers' sampler then draws from a
        # degenerate distribution, so the global RNG cannot change the result.
        forced = torch.full_like(scores, torch.finfo(scores.dtype).min)
        forced.scatter_(1, chosen.unsqueeze(1), 0)
        return forced

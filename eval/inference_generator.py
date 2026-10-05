"""Bridge the local inference service to the trajectory factory's generator contract.

Two things live here and they are both provenance, not plumbing:

- `ServeGenerator` turns one served completion into a `trajectory_factory.Proposal` carrying
  a full durable receipt. Without it the factory would be back to the state the September 19
  review found: 29 attempts with no prompt, no model and no raw response.
- `ModelIdentity` records which WEIGHTS served a run. A fine-tune claim that cannot name the
  bytes it was compared against is not a claim, so the checkpoint path, the adapter hash the
  service reports, and the sampler settings are captured together and written into the run
  report.

The extraction rules are the project's own (`solver.llm`): refusals are their own population,
prose is an extraction failure rather than a model error, and echoed assembly is never handed
to the compiler. Those rules were each paid for in production, and re-implementing them here
would be a second, drifting copy.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from solver import llm


def derive_seed(base: int | None, function: str, role: str, index: int) -> int | None:
    """A per-call seed derived from the function, the role and the draw index.

    An explicit seed makes a request REPRODUCIBLE at the server, which is what a run wants for
    a result it can defend -- and is exactly what makes N draws sharing one seed a single
    draw reported N times. Deriving it per call gives both properties at once. `None` stays
    `None`: an unseeded run is stochastic, which is also a valid configuration.
    """
    if base is None:
        return None
    material = f"{base}:{function}:{role}:{index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big") % (2 ** 31 - 1)


@dataclass
class ModelIdentity:
    """Which artifact served a run, in a form a report can quote."""

    model_path: str = ""
    adapter_path: str = ""
    adapter_hash: str = ""
    served_model: str = ""
    checkpoint_sha256: str = ""
    prompt_version: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"model_path": self.model_path, "adapter_path": self.adapter_path,
                "adapter_hash": self.adapter_hash, "served_model": self.served_model,
                "checkpoint_sha256": self.checkpoint_sha256,
                "prompt_version": self.prompt_version, **self.extra}


def directory_digest(path: Path | str, *, limit_bytes: int = 64 * 1024 * 1024) -> str:
    """A digest over a checkpoint directory's small files, which is what identifies it.

    Shard files are included; only files at or above `limit_bytes` are skipped, and the count
    of skipped files is not hidden -- it is appended to the digest material as a marker so a
    directory with one skipped shard cannot collide with one that has two.
    """
    import hashlib
    root = Path(path)
    if not root.exists():
        return ""
    hasher = hashlib.sha256()
    skipped = 0
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        if item.stat().st_size >= limit_bytes:
            skipped += 1
            hasher.update(f"SKIPPED:{item.name}:{item.stat().st_size}\n".encode())
            continue
        hasher.update(item.name.encode("utf-8"))
        hasher.update(item.read_bytes())
    hasher.update(f"skipped={skipped}\n".encode())
    return hasher.hexdigest()


class ServeGenerator:
    """The real proposer: the local served checkpoint, with a receipt per call.

    `role` is passed through to the sampler so a repair draw can differ from a draft draw
    without either call site inventing its own temperature policy.
    """

    def __init__(self, *, endpoint: str, model: str, adapter: str | None = None,
                 max_tokens: int = 3000, top_p: float = 0.95, top_k: int = 0,
                 seed: int | None = None, prefill: str = "```c\n",
                 timeout: float = 900.0, think: str = "low"):
        from tools.lora_serve.client import InferenceClient
        self.client = InferenceClient(base_url=endpoint, timeout=timeout,
                                      receipt=True, require_receipt=False,
                                      prefill=prefill)
        self.model = model
        self.adapter = adapter or ""
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.top_k = top_k
        self.seed = seed
        self.prefill = prefill
        self.think = think
        self.endpoint = endpoint
        self.function = ""
        self.refusals = 0
        self.dropped = 0
        self.errors: list[str] = []
        self.raw_heads: list[str] = []
        self.receipts: list = []
        self.identity = ModelIdentity(
            model_path=model, adapter_path=self.adapter,
            prompt_version=__import__("eval.repair_prompts", fromlist=["x"]).PROMPT_VERSION)
        self._identity_probed = False

    # -- identity ---------------------------------------------------------
    def begin_function(self, name: str) -> None:
        """The factory names the function before its draws, so seeds can be derived from it."""
        self.function = name

    def probe_identity(self) -> ModelIdentity:
        """Ask the service what it is actually serving, once."""
        if self._identity_probed:
            return self.identity
        self._identity_probed = True
        try:
            models = self.client.models(timeout=30.0)
            if models:
                entry = models[0]
                self.identity.served_model = str(entry.get("id") or entry.get("name") or "")
                for key in ("adapter_hash", "adapter", "checkpoint_sha256", "model_path"):
                    if entry.get(key):
                        self.identity.extra[key] = entry[key]
                        if key == "adapter_hash":
                            self.identity.adapter_hash = str(entry[key])
        except Exception as exc:  # the identity probe must never break a run
            self.identity.extra["identity_probe_error"] = f"{type(exc).__name__}: {exc}"
        if not self.identity.checkpoint_sha256:
            self.identity.checkpoint_sha256 = directory_digest(self.model)
        return self.identity

    # -- the factory contract --------------------------------------------
    def draw(self, prompt: str, n: int, temperature: float, *,
             role: str = "independent", deadline: float | None = None):
        """N calls, each returning a Proposal WITH a receipt -- including failures.

        A failed call still returns a Proposal so the budget accounting sees it. The old
        generator `break`-ed out of the loop on the first exception, which spent the round's
        remaining allowance on nothing and reported it as a short round.
        """
        from eval.trajectory_factory import Proposal, RepairState

        self.probe_identity()
        # A REPAIR DRAW MUST CARRY A PARENT OBJECT, or the factory has nothing to attach the
        # parent's receipt id to. The repair PROMPT was already rendered from the recorded
        # state before this call, so the id is filled in by the factory -- but a generator that
        # omits the object leaves `proposal.parent` None, and the attempt is written with no
        # parent at all. That is a repair with no edge: the data looks like a repair (the prompt
        # says so, the score moved) and carries none of the lineage that makes it training data.
        # Measured 2026-09-20 in the collection pilot: 8 repair calls, every one stored with
        # `parent_attempt_id` NULL. The field is intentionally left without an id here; the
        # factory owns that assignment so parentage stays a property of the REQUEST.
        parent = RepairState(source="", compiled=True) if role == "repair" else None
        out = []
        for index in range(n):
            if deadline is not None and time.monotonic() >= deadline:
                out.append(Proposal(prompt=prompt, action=role, error="timeout-deadline"))
                break
            started = time.time()
            text, meta, error = "", {}, ""
            # A per-call seed. The service treats an explicit `seed` as FIXED-SEED sampling,
            # so passing one value for every draw makes each draw the same generation. The
            # 2026-09-20 pilot did that and reported 68 independent draws that were 17 distinct
            # generations, and a 0/12 repair yield that was one output per function repeated
            # four times. Deriving the seed per (function, role, index) keeps the run
            # reproducible while making the draws independent.
            call_seed = derive_seed(self.seed, self.function, role, index)
            try:
                result = self.client.chat(
                    [{"role": "user", "content": prompt}],
                    prefill=self.prefill, echo_prefill=True,
                    temperature=temperature, top_p=self.top_p,
                    top_k=self.top_k or None, seed=call_seed,
                    max_tokens=self.max_tokens, timeout=None,
                    receipt=True)
                text = result.text or ""
                meta = dict(result.receipt or {})
                meta.setdefault("model", result.model)
                meta.setdefault("eval_count", result.completion_tokens)
                meta.setdefault("prompt_eval_count", result.prompt_tokens)
                meta.setdefault("done_reason", result.finish_reason)
                if result.request_id:
                    meta.setdefault("request_id", result.request_id)
                if result.adapter_hash:
                    meta.setdefault("adapter_hash", result.adapter_hash)
                    self.identity.adapter_hash = result.adapter_hash
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.errors.append(error)
            wall_ms = int((time.time() - started) * 1000)
            self.raw_heads.append((text or "")[:200])

            code = ""
            if not error and not llm.is_refusal(text or ""):
                candidate = llm.extract_c(text or "")
                if candidate and llm.FUNC_DEF_RE.search(candidate):
                    code = candidate

            receipt = llm.generation_receipt(
                text, meta, prompt=prompt, model=self.identity.served_model or self.model,
                extracted=code,
                sampling={"temperature": temperature, "top_p": self.top_p,
                          "top_k": self.top_k, "max_tokens": self.max_tokens,
                          "seed": self.seed, "role": role, "draw_index": index,
                          "prefill": self.prefill, "adapter": self.adapter,
                          "adapter_hash": self.identity.adapter_hash},
                wall_ms=wall_ms, error=error)
            if receipt.status == "refusal":
                self.refusals += 1
            elif receipt.status in ("no-extract", "empty"):
                self.dropped += 1
            self.receipts.append(receipt)
            out.append(Proposal(source=code, prompt=prompt, action=role,
                                receipt=receipt, error=error, parent=parent))
        return out

    def sample(self, prompt: str, n: int, temperature: float) -> list[str]:
        return [p.source for p in self.draw(prompt, n, temperature) if p.source]


def load_arm_client(arm: dict, *, endpoint: str, **kwargs) -> ServeGenerator:
    """Build a generator for one preregistered arm."""
    return ServeGenerator(endpoint=endpoint,
                          model=arm["checkpoint"]["path"],
                          adapter=(arm.get("adapter") or {}).get("path"),
                          **kwargs)

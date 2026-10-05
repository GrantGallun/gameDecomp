"""Drive the whole post-training experiment: canary, train, evaluate, compare.

WHY THIS IS IN-PROCESS RATHER THAN THROUGH A SERVER
---------------------------------------------------
The evaluation has to serve M0 (bare checkpoint) and M1 (checkpoint + LoRA) with IDENTICAL
transport, because the only difference permitted between the arms is the weights. A separate
serving process per arm leaves two ways to violate that: the two servers can be configured
differently, and swapping the adapter in and out of a long-lived process is exactly the kind
of state the arms must not share.

Loading the checkpoint once, generating for BOTH arms in the same process, and enabling the
adapter for exactly one of them removes that class of mistake. It also removes a dependency on
a serving stack whose behaviour under batching is not this experiment's subject.

WHAT THE ARMS SHARE, EXPLICITLY
-------------------------------
Same model object, same tokenizer, same sampler, same prompt, same order of functions, same
oracle, same attempt budget. `--arm` generation order is interleaved per function so a
time-varying machine (thermal throttling, another job arriving) cannot favour one arm.

REFERENCE IMPLEMENTATION PARITY
-------------------------------
Sampling and chat rendering mirror `tools.lora_serve`: chat template for the prompt, a partial
assistant turn (`solver.refine`'s prefill convention, which the project measured stopping
refusals outright), and the project's own `solver.llm.extract_c` for extraction. A third
extraction implementation would be a third set of bugs.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PREFILL = "```c\n"
SYSTEM = ""


@dataclass
class Sampler:
    temperature: float = 0.8
    top_p: float = 0.95
    top_k: int = 0
    repetition_penalty: float = 1.05
    max_new_tokens: int = 3000
    seed: int | None = None


class LocalModel:
    """One loaded checkpoint, with an optional adapter that can be toggled on and off.

    The adapter is DISABLED by default. A generator that defaults to the trained adapter
    cannot serve the control arm, and a control arm that accidentally carries the adapter
    makes every comparison a null result reported as a real one.
    """

    def __init__(self, model_path: Path, *, device: str = "cuda:0",
                 dtype: str = "bfloat16", max_model_len: int = 12288,
                 load_in_4bit: bool = True):
        # Caps before the first CUDA allocation. See `eval.resource_limits`; the defaults
        # leave half the card and half the cores free for the rest of the machine.
        from eval import resource_limits
        self.limits = resource_limits.apply()

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.torch = torch
        self.model_path = str(model_path)
        self.device = device
        self.max_model_len = max_model_len
        self.load_in_4bit = bool(load_in_4bit)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        # 4-bit by default. The checkpoint is 14.2 GiB of bf16 weights against a 16 GB card
        # shared with a Windows ollama server, so bf16 does not fit inside any sane per-process
        # cap -- and the honest measurement of a model on a shared box is the model as it can
        # actually be run. It is identical for both arms and is recorded in every receipt.
        quantisation = None
        if self.load_in_4bit:
            from transformers import BitsAndBytesConfig
            quantisation = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_path, dtype=getattr(torch, dtype),
            quantization_config=quantisation,
            device_map=(device if quantisation else None))
        if not quantisation:
            self.model.to(device)
        self.model.eval()
        self.adapter_path = ""
        self.adapter_active = False

    # -- adapters ---------------------------------------------------------
    def load_adapter(self, path: Path) -> dict:
        import hashlib
        from peft import PeftModel

        self.model = PeftModel.from_pretrained(self.model, str(path),
                                               adapter_name="arm", is_trainable=False)
        self.model.eval()
        self.adapter_path = str(path)
        self.adapter_active = False          # loading is not enabling
        digest = hashlib.sha256()
        for item in sorted(Path(path).rglob("*")):
            if item.is_file() and item.stat().st_size < 64 * 1024 * 1024:
                digest.update(item.name.encode())
                digest.update(item.read_bytes())
        return {"adapter_path": str(path), "adapter_sha256": digest.hexdigest()}

    def set_adapter(self, active: bool) -> None:
        if not self.adapter_path:
            if active:
                raise RuntimeError("no adapter is loaded; the M1 arm cannot be served")
            return
        self.model.set_adapter("arm" if active else None)
        self.adapter_active = active

    def identity(self) -> dict:
        return {"model_path": self.model_path,
                "adapter_path": self.adapter_path if self.adapter_active else "",
                "adapter_active": self.adapter_active,
                "load_in_4bit": self.load_in_4bit,
                "max_model_len": self.max_model_len}

    # -- generation -------------------------------------------------------
    def render(self, prompt: str) -> list[int]:
        ids = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], tokenize=True,
            add_generation_prompt=True)
        if isinstance(ids, dict):
            ids = ids["input_ids"]
        elif hasattr(ids, "input_ids"):
            ids = ids.input_ids
        ids = list(ids)
        prefill_ids = self.tokenizer(PREFILL, add_special_tokens=False)["input_ids"]
        return ids + list(prefill_ids)

    def generate(self, prompt: str, sampler: Sampler) -> dict:
        """One completion, with the receipt the factory needs."""
        import solver.llm as llm

        torch = self.torch
        input_ids = self.render(prompt)
        if len(input_ids) >= self.max_model_len:
            return {"text": "", "status": "error",
                    "error": f"prompt of {len(input_ids)} tokens exceeds the "
                             f"{self.max_model_len}-token context"}
        budget = min(sampler.max_new_tokens, self.max_model_len - len(input_ids))
        seed = sampler.seed
        if seed is not None:
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
        tensor = torch.tensor([input_ids], device=self.device)
        attention = torch.ones_like(tensor)
        started = time.time()
        try:
            with torch.no_grad():
                out = self.model.generate(
                    input_ids=tensor, attention_mask=attention,
                    max_new_tokens=budget, do_sample=True,
                    temperature=max(sampler.temperature, 1e-5), top_p=sampler.top_p,
                    top_k=(sampler.top_k or 0),
                    repetition_penalty=sampler.repetition_penalty,
                    pad_token_id=self.tokenizer.pad_token_id,
                    eos_token_id=self.tokenizer.eos_token_id)
        except Exception as exc:
            return {"text": "", "status": "error",
                    "error": f"{type(exc).__name__}: {exc}"}
        wall_ms = int((time.time() - started) * 1000)
        new_ids = out[0][len(input_ids):].tolist()
        text = PREFILL + self.tokenizer.decode(new_ids, skip_special_tokens=True)
        error = ""
        extracted = ""
        if not llm.is_refusal(text):
            candidate = llm.extract_c(text)
            if candidate and llm.FUNC_DEF_RE.search(candidate):
                extracted = candidate
        receipt = llm.generation_receipt(
            text, {"eval_count": len(new_ids), "prompt_eval_count": len(input_ids),
                   "done_reason": "stop" if new_ids and new_ids[-1] == self.tokenizer.eos_token_id
                   else "length",
                   "model": Path(self.model_path).name,
                   "adapter": self.adapter_path if self.adapter_active else ""},
            prompt=prompt, model=Path(self.model_path).name, extracted=extracted,
            sampling={"temperature": sampler.temperature, "top_p": sampler.top_p,
                      "top_k": sampler.top_k, "max_new_tokens": budget, "seed": seed,
                      "adapter_active": self.adapter_active},
            wall_ms=wall_ms, error=error)
        return {"text": text, "extracted": extracted, "receipt": receipt,
                "input_tokens": len(input_ids), "output_tokens": len(new_ids),
                "wall_ms": wall_ms}


class InProcessGenerator:
    """The factory's `draw` contract over `LocalModel`."""

    def __init__(self, model: LocalModel, sampler: Sampler, *, arm: str = ""):
        self.model = model
        self.sampler = sampler
        self.arm = arm
        self.function = ""
        self.refusals = 0
        self.errors: list[str] = []
        self.dropped = 0
        self.raw_heads: list[str] = []
        self.receipts: list = []
        self.rows: list[dict] = []

    def begin_function(self, name: str) -> None:
        """The factory names the function before its draws, so seeds can be derived from it."""
        self.function = name

    def model_digest(self) -> str:
        return self.model.identity().get("adapter_path", "") or "base"

    def draw(self, prompt: str, n: int, temperature: float, *,
             role: str = "independent", deadline: float | None = None):
        from eval.trajectory_factory import Proposal

        out = []
        for index in range(n):
            if deadline is not None and time.monotonic() >= deadline:
                out.append(Proposal(prompt=prompt, action=role, error="timeout-deadline"))
                break
            # Per-call seed. A single shared seed makes every draw the same generation and
            # reports best-of-N as best-of-1 -- measured on the 2026-09-20 collection pilot.
            # See `eval.inference_generator.derive_seed`.
            from eval.inference_generator import derive_seed
            seed = derive_seed(self.sampler.seed, self.function, role, index)
            sampler = Sampler(temperature=temperature, top_p=self.sampler.top_p,
                              top_k=self.sampler.top_k,
                              repetition_penalty=self.sampler.repetition_penalty,
                              max_new_tokens=self.sampler.max_new_tokens,
                              seed=seed)
            result = self.model.generate(prompt, sampler)
            text = result.get("text", "")
            self.raw_heads.append((text or "")[:200])
            if result.get("status") == "error":
                self.errors.append(result["error"])
            receipt = result.get("receipt")
            if receipt is not None:
                if receipt.status == "refusal":
                    self.refusals += 1
                elif receipt.status in ("no-extract", "empty"):
                    self.dropped += 1
                self.receipts.append(receipt)
            row = {"arm": self.arm, "role": role, "draw_index": index,
                   "input_tokens": result.get("input_tokens"),
                   "output_tokens": result.get("output_tokens"),
                   "wall_ms": result.get("wall_ms"),
                   "status": (receipt.status if receipt is not None else "error"),
                   "error": result.get("error", ""),
                   "extracted_chars": len(result.get("extracted") or ""),
                   "prompt_sha256": (receipt.as_row()["prompt_sha256"]
                                     if receipt is not None else None)}
            self.rows.append(row)
            out.append(Proposal(source=result.get("extracted") or "", prompt=prompt,
                                action=role, receipt=receipt,
                                error=result.get("error", "")))
        return out

    def sample(self, prompt: str, n: int, temperature: float) -> list[str]:
        return [p.source for p in self.draw(prompt, n, temperature) if p.source]

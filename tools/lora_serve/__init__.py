"""Local LoRA inference service for the matching-decompilation project.

Two pieces, deliberately separated:

* ``tools.lora_serve.server`` -- an OpenAI-compatible HTTP service over a local
  HuggingFace checkpoint plus any number of PEFT LoRA adapters, with complete
  generation receipts.
* ``tools.lora_serve.client`` -- a dependency-free client (stdlib only, no torch)
  that other repo tools can import.

The client is the supported entry point for other tools::

    from tools.lora_serve import InferenceClient
    client = InferenceClient("http://127.0.0.1:8100")
    result = client.chat(messages, prefill="```c\\n", temperature=0.2, seed=7)
    print(result.text, result.receipt["prompt_tokens"])
"""

from tools.lora_serve.client import (  # noqa: F401
    AdapterNotFound,
    InferenceClient,
    InferenceError,
    ServerNotRunning,
    GenerationResult,
)

__all__ = [
    "InferenceClient",
    "GenerationResult",
    "InferenceError",
    "ServerNotRunning",
    "AdapterNotFound",
]

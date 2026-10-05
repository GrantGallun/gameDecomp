"""Chat rendering and prefill semantics for the local inference service.

This module is deliberately torch-free and server-free: it is the part of the
service whose behaviour can be verified without a GPU, and it is where the
repo's prefill trick lives.

Why prefill is a first-class field here
---------------------------------------
``solver/llm.py`` documents the measurement: a partial ASSISTANT turn suppresses
refusals (9/9 -> 0/9 on functions that refuse every draw), while appending the
same text to the USER turn does nothing (18/18 still refused). Which turn the
text lands in is the whole effect. An OpenAI ``/v1/chat/completions`` request
cannot express "the assistant turn has already started, continue it", so this
service accepts two explicit spellings:

* ``"prefill": "```c\\n"`` -- service extension, explicit and unambiguous.
* ``"continue_final_message": true`` with a final ``assistant`` message --
  the spelling vLLM's OpenAI server uses, accepted here for compatibility.

Both produce the identical rendered prompt (measured, see
``evidence.rendering_equivalence`` in the results README).

Measured facts about THIS checkpoint (base ``qwen2.5-coder-7b``), 2026-09-20:

* ``tokenizer_config.json`` carries the standard Qwen2 chat template, which
  injects a default system turn (``You are Qwen, created by Alibaba Cloud. You
  are a helpful assistant.``) whenever ``messages[0]`` is not a system turn.
  That injected text is part of the prompt the model actually sees, so it is
  part of the receipt.
* ``render(history, add_generation_prompt=True) + prefill`` is byte-identical to
  transformers' ``apply_chat_template(history + [assistant(prefill)],
  continue_final_message=True)``.
* The naive rendering of the full history (``add_generation_prompt=False``)
  CLOSES the assistant turn with ``<|im_end|>\\n`` -- the model would then start a
  new turn instead of continuing the prefix. That is the trap this module
  exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "ChatRenderError",
    "RenderedPrompt",
    "normalize_messages",
    "render_chat",
    "render_completion",
]


class ChatRenderError(ValueError):
    """The request cannot be rendered into a prompt."""


@dataclass(frozen=True)
class RenderedPrompt:
    """The exact prompt string handed to the model, plus how it was produced."""

    text: str
    """Full prompt text, INCLUDING the prefill when there is one."""

    prefill: str
    """The partial assistant turn spliced onto the end ("" when none)."""

    history_text: str
    """Prompt text before the prefill (== text when there is no prefill)."""

    templated: bool
    """True when a chat template produced ``history_text``."""

    template_source: str
    """``tokenizer`` | ``explicit`` | ``none`` -- where the template came from."""

    messages: tuple[dict[str, str], ...] = ()
    """The normalized conversation that was rendered (prefill message excluded)."""

    notes: tuple[str, ...] = ()
    """Non-fatal observations worth recording in the receipt."""

    @property
    def continuation_start(self) -> str:
        """The text the model must continue from (the prefill)."""
        return self.prefill

    def receipt_fields(self) -> dict[str, Any]:
        return {
            "rendered_prompt": self.text,
            "rendered_history": self.history_text,
            "prefill": self.prefill,
            "templated": self.templated,
            "template_source": self.template_source,
            "messages": [dict(m) for m in self.messages],
            "render_notes": list(self.notes),
        }


def _content_to_text(content: Any) -> str:
    """Flatten an OpenAI message content into plain text.

    OpenAI allows ``content`` to be a list of parts (``{"type": "text", ...}``).
    Non-text parts are rejected rather than silently dropped: a dropped image
    would make the receipt a lie about what the model saw.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, Mapping):
                if part.get("type") in (None, "text") and "text" in part:
                    chunks.append(str(part["text"]))
                else:
                    raise ChatRenderError(
                        f"unsupported content part type {part.get('type')!r}; "
                        "only text parts are supported by this local service")
            else:
                raise ChatRenderError(f"unsupported content part {part!r}")
        return "".join(chunks)
    raise ChatRenderError(f"unsupported message content type {type(content).__name__}")


def normalize_messages(messages: Iterable[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    """Validate and copy an OpenAI-style message list."""
    if not messages:
        raise ChatRenderError("messages must be a non-empty list")
    out: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise ChatRenderError(f"messages[{index}] is not an object")
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            raise ChatRenderError(
                f"messages[{index}].role must be one of system/user/assistant/tool, "
                f"got {role!r}")
        if role == "assistant" and message.get("tool_calls"):
            # The Qwen template renders tool_calls into <tool_call> blocks. This
            # service does not implement tool calling; accepting the message would
            # silently render a turn the caller did not ask for.
            raise ChatRenderError("assistant tool_calls are not supported by this service")
        out.append({"role": str(role), "content": _content_to_text(message.get("content"))})
    return out


def _no_template_render(messages: Sequence[Mapping[str, str]]) -> str:
    """Fallback rendering used only when explicitly allowed and no template exists.

    Recorded in the receipt as ``template_source="none"`` so no downstream
    consumer can mistake it for template output.
    """
    return "".join(f"{m['role']}: {m['content']}\n" for m in messages)


def render_chat(
    tokenizer: Any,
    messages: Sequence[Mapping[str, Any]],
    *,
    prefill: str | None = None,
    continue_final_message: bool = False,
    chat_template: str | None = None,
    add_generation_prompt: bool = True,
    allow_missing_template: bool = False,
) -> RenderedPrompt:
    """Render an OpenAI chat request into the exact prompt string.

    ``prefill`` and ``continue_final_message`` are mutually exclusive ways to say
    the same thing; passing both is an error rather than a silent precedence
    rule, because guessing here would silently change which turn the text lands
    in -- the exact confusion ``solver/llm.py`` records as having been
    mis-modelled twice.
    """
    normalized = normalize_messages(messages)
    notes: list[str] = []

    if prefill is not None and continue_final_message:
        raise ChatRenderError(
            "pass either 'prefill' or 'continue_final_message', not both")
    if prefill is not None and not isinstance(prefill, str):
        raise ChatRenderError("prefill must be a string")
    if continue_final_message:
        if normalized[-1]["role"] != "assistant":
            raise ChatRenderError(
                "continue_final_message requires the final message to have role "
                f"'assistant', got {normalized[-1]['role']!r}")
        prefill = normalized[-1]["content"]
        normalized = normalized[:-1]
        if not normalized:
            raise ChatRenderError(
                "continue_final_message requires at least one message before the "
                "final assistant turn")

    template = chat_template
    source = "explicit"
    if template is None:
        template = getattr(tokenizer, "chat_template", None)
        source = "tokenizer" if template else "none"

    if template:
        try:
            history_text = tokenizer.apply_chat_template(
                list(normalized), tokenize=False,
                add_generation_prompt=bool(add_generation_prompt),
                chat_template=template)
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            raise ChatRenderError(
                f"chat template failed to render: {type(exc).__name__}: {exc}") from exc
    elif allow_missing_template:
        history_text = _no_template_render(normalized)
        source = "none"
        notes.append(
            "tokenizer has no chat template; rendered with the plain "
            "'role: content' fallback (allow_missing_template=True)")
    else:
        raise ChatRenderError(
            "this tokenizer has no chat template; pass 'chat_template' explicitly "
            "or run the server with --allow-missing-chat-template to use the "
            "documented plain-text fallback")

    if not add_generation_prompt:
        notes.append(
            "add_generation_prompt=False: the assistant turn header was NOT "
            "appended; the model continues the raw text as given")

    text = history_text + (prefill or "")
    if prefill:
        notes.append(
            "prefill spliced after the generation prompt; the returned text is "
            "prefill + continuation and the receipt separates them")
    return RenderedPrompt(
        text=text, prefill=prefill or "", history_text=history_text,
        templated=bool(template), template_source=source,
        messages=tuple(normalized), notes=tuple(notes))


def render_completion(prompt: str | Sequence[str]) -> RenderedPrompt:
    """Render a legacy ``/v1/completions`` request: raw text, no chat template."""
    if isinstance(prompt, (list, tuple)):
        if len(prompt) != 1:
            raise ChatRenderError(
                "batch prompts are not supported; send one prompt per request")
        prompt = prompt[0]
    if not isinstance(prompt, str) or not prompt:
        raise ChatRenderError("'prompt' must be a non-empty string")
    return RenderedPrompt(
        text=prompt, prefill="", history_text=prompt, templated=False,
        template_source="none", messages=(),
        notes=("raw completion prompt: no chat template applied",))

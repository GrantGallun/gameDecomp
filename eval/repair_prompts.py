"""The one place a source-repair training input is written down.

WHY THIS MODULE EXISTS
----------------------
A repair example has to be the same string whether it is being (a) sent to a model during
data collection, (b) stored as a training completion, or (c) sent to a model during
evaluation. Three call sites constructing "roughly the same prompt" is how a fine-tune ends
up trained on a distribution it is never evaluated on, and the failure is invisible: the
loss goes down and the match rate does not move.

Components are reused, not re-derived:

- The LEAF prompt is `solver.refine.FIRST_PROMPT`, the project's own, on the same
  `asm / draft / kb / hints` interface the campaign uses.
- The REPAIR prompt is `eval.trajectory_factory.render_repair_prompt`, which itself reuses
  `solver.refine.DIFF_PROMPT` and `COMPILE_FAIL_PROMPT`.
- Header assistance is the `include/game/**` projection the repo already produces. It is a
  PROVENANCE TIER, not a decoration: an answer produced with a helper header is reported
  separately from a binary-only answer, because the header can carry the decomp team's own
  prototype and struct layout.

`PROMPT_VERSION` is folded into every stored record so a dataset can be rejected when the
rendering changes rather than silently mixed with a newer one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROMPT_VERSION = "repair-v2"

LEAF_KIND = "leaf"
REPAIR_KIND = "repair"
# A repair against a SYNTHETIC task: a generated function compiled by the real IDO recipe, with a
# candidate that is a damaged copy of the generator's own source. Distinguished from `repair`
# because the two carry different provenance and must never be pooled in a report.
SYNTHETIC_KIND = "synthetic-repair"

# The synthetic repair instruction. Deliberately parallel to `solver.refine.DIFF_PROMPT`, which
# the game-function route uses, so the model sees the same task stated the same way in both
# curricula -- but built here because the synthetic task has no m2c draft and no project headers.
SYNTHETIC_REPAIR_TEMPLATE = """\
You are repairing a C function so that recompiling it reproduces a target object exactly.
The compiler is IDO 5.3 targeting MIPS at -O2.

TARGET ASSEMBLY -- the instructions your C must produce:
```
{asm}
```

CURRENT CANDIDATE -- it compiles, but its instructions do not match the target:
```c
{candidate}
```

COMPILER FEEDBACK on the candidate above:
```
{feedback}
```

Produce the corrected C. Reasons a candidate's code differs from the target:
- a value declared too narrow (`s16` where `s32` was written) is sign-extended at every use
- loop form changes the entry test and where the step is emitted: `for` is not `while`
- an intermediate local variable changes register allocation and instruction order
- a missing or extra `default:` changes the compare chain IDO emits
- signed division (`/ 2^n`) emits a bias and a shift that `>> n` does not

Output ONE self-contained C file in a single ```c code block. No prose. It may only
#include "common.h", which already defines u8, s8, u16, s16, u32, s32, u64, s64, f32, f64.
Supply any OTHER types and every extern declaration INLINE, defining a type BEFORE any
declaration that uses it. C89 only.
"""


def synthetic_repair_prompt(*, asm: str, candidate: str, feedback: str) -> str:
    """The single construction site for a synthetic repair prompt."""
    return SYNTHETIC_REPAIR_TEMPLATE.format(asm=asm, candidate=candidate, feedback=feedback)


# Compiler-logic tasks (2026-10-03, eval/logic_tasks.py): contrastive C pairs labelled by the compiler itself.
# PREDICT teaches the forward rule (does this spelling change the code, and which rows); EXPLAIN teaches reading a
# residual back to the source edit that causes it. Both are game-agnostic: they are about the compiler.
LOGIC_PREDICT_KIND = "logic-predict"
LOGIC_EXPLAIN_KIND = "logic-explain"
LOGIC_NEED_KIND = "logic-need"
# v2 (2026-10-03): every logic prompt carries the declarations the compiler needed, re-checked by compiling them
# with the function alone (eval/results/edit-capability-20261002/context_tasks.py). v1 prompts had none and some of
# their labels were underdetermined (audit 2026-10-03).
LOGIC_PROMPT_VERSION = "logic-v3"
# READ (2026-10-04, eval/results/edit-capability-20261002/reading_tasks.py): one statement is blanked and the target
# instructions the compiler's line table attributes to it are marked; the answer is that statement. The narrow
# reading skill (store -> lvalue = value, call setup -> callee and arguments, value vs &local) that missing-statement
# repairs need, isolated from localization. Graded by substituting the answer and compiling.
LOGIC_READ_KIND = "logic-read"
# EXAMINER (2026-10-05, self_curriculum.py examiner): the examiner's own proposals that earned a learnability reward
# (prompt -> its exact edit). Rendered prompt/completion like the logic kinds, so the same loader trains it.
EXAMINER_KIND = "examiner-propose"
LOGIC_KINDS = (LOGIC_PREDICT_KIND, LOGIC_EXPLAIN_KIND, LOGIC_NEED_KIND, LOGIC_READ_KIND, EXAMINER_KIND)

_CONTEXT_BLOCK = """\
DECLARATIONS IN SCOPE (preprocessed; with these the functions compile exactly as in their original file):
```c
{context}
```
"""

LOGIC_PREDICT_TEMPLATE = """\
Compiler: {compiler} ({opt}), MIPS.
""" + _CONTEXT_BLOCK + """
Function A and function B differ only on the lines marked `>>`. Does the compiler emit the same instructions
for both? Answer SAME, or DIFFER followed by the instruction rows that change (`-` rows are A's, `+` rows B's).
{need_rule}
FUNCTION A:
```c
{a}
```

FUNCTION B:
```c
{b}
```
"""

LOGIC_EXPLAIN_TEMPLATE = """\
Compiler: {compiler} ({opt}), MIPS.
""" + _CONTEXT_BLOCK + """
This function compiles, but its instructions differ from the target: `-` rows are the target's, `+` rows are
this function's. Give the source edit that makes them equal, one edit per line, line numbers as shown:
REPLACE n: <new text> | INSERT AFTER n: <new text> | DELETE n

```c
{numbered}
```

INSTRUCTION DIFF:
```
{diff}
```
"""


LOGIC_READ_TEMPLATE = """\
Compiler: {compiler} ({opt}), MIPS.
""" + _CONTEXT_BLOCK + """
Line {line} of this function has been blanked (`/* ? */`). The function's compiled instructions follow; the rows
marked `>` are the ones the compiler's line table attributes to line {line}. Write the C statement that belongs on
line {line}, and nothing else.

```c
{numbered}
```

INSTRUCTIONS:
```
{listing}
```
"""


# DECOMPILE (2026-10-04, decompile_tasks.py): the regression exam of general work -- the whole function from its
# instructions. Never a training kind (not in LOGIC_KINDS), so narrow pilots are checked against it, not fitted to it.
DECOMPILE_KIND = "decompile"

DECOMPILE_TEMPLATE = """\
Compiler: {compiler} ({opt}), MIPS.
""" + _CONTEXT_BLOCK + """
Write the C function `{signature}` so that it compiles to exactly these instructions. Give the whole function
definition in one ```c block.

INSTRUCTIONS:
```
{listing}
```
"""


def decompile_prompt(*, compiler: str, opt: str, context: str, signature: str, listing: str) -> str:
    return DECOMPILE_TEMPLATE.format(compiler=compiler, opt=opt, context=context.rstrip(), signature=signature,
                                     listing=listing)


LOGIC_READ_MULTI_TEMPLATE = """\
Compiler: {compiler} ({opt}), MIPS.
""" + _CONTEXT_BLOCK + """
Lines {lines} of this function have been blanked (`/* ? */`). The function's compiled instructions follow; a row
marked `N>` is one the compiler's line table attributes to line N. Write the C statement for every blanked line, one
per line, as `Line N: <statement>`, and nothing else.

```c
{numbered}
```

INSTRUCTIONS:
```
{listing}
```
"""


def logic_read_multi_prompt(*, compiler: str, opt: str, context: str, numbered: str, lines: list[int],
                            listing: str) -> str:
    return LOGIC_READ_MULTI_TEMPLATE.format(compiler=compiler, opt=opt, context=context.rstrip(), numbered=numbered,
                                            lines=", ".join(map(str, lines)), listing=listing)


def logic_read_prompt(*, compiler: str, opt: str, context: str, numbered: str, line: int, listing: str) -> str:
    return LOGIC_READ_TEMPLATE.format(compiler=compiler, opt=opt, context=context.rstrip(), numbered=numbered,
                                      line=line, listing=listing)


NEED_RULE = ("A type shown as `??` has been withheld. If the answer depends on it, answer only `NEED: <name>` with the "
             "declared name whose type you need; if it does not, answer as usual.\n")


def logic_predict_prompt(*, compiler: str, opt: str, context: str, a: str, b: str, withheld: bool = False,
                         type_options: tuple[str, str] | None = None) -> str:
    need_rule = NEED_RULE if withheld else ""
    if withheld:
        if not type_options:
            raise ValueError("withheld types require the two compiler-tested alternatives")
        first, second = sorted(type_options)
        need_rule += (f"The withheld type is `{first}` or `{second}`; consider only these two possibilities.\n")
    return LOGIC_PREDICT_TEMPLATE.format(compiler=compiler, opt=opt, context=context.rstrip(), a=a, b=b,
                                         need_rule=need_rule)


LOGIC_RETRY_TEMPLATE = ("Compiler feedback on that attempt:\n{feedback}\n\nGive a corrected edit script for the function "
                        "as originally shown (same line numbers), in the same format.")


def logic_retry_prompt(feedback: str, *, worse_than: tuple | None = None) -> str:
    """The multi-turn retry turn (eval/train_grpo.py and eval/logic_exam.py both use it). `worse_than` =
    (attempt number, its edit script, its feedback) when the latest attempt leaves more instruction rows different
    than an earlier one: that earlier attempt is SHOWN alongside, and the model chooses where to continue from."""
    if worse_than is None:
        return LOGIC_RETRY_TEMPLATE.format(feedback=feedback)
    # Information, not an order: the evolvability trials (eval/results/evolvability-trial-20260928) found searches
    # EXPLORE sources closer to the answer in 51-67 of 77 functions but KEEP them in 6-10, because the
    # differing-row count does not recognise those steps. So the closest attempt is shown, not imposed.
    n, best_text, best_feedback = worse_than
    return (f"Compiler feedback on that attempt:\n{feedback}\n\nFor reference, your attempt {n} left fewer "
            f"instruction rows different:\n{best_text}\n\nCompiler feedback on attempt {n}:\n{best_feedback}\n\n"
            "Fewer differing rows is not always closer to the fix: continue from whichever attempt you judge "
            "right. Give a corrected edit script for the function as originally shown (same line numbers), in the "
            "same format.")


def logic_explain_prompt(*, compiler: str, opt: str, context: str, numbered: str, diff: str) -> str:
    return LOGIC_EXPLAIN_TEMPLATE.format(compiler=compiler, opt=opt, context=context.rstrip(), numbered=numbered,
                                         diff=diff)


def load_task_examples(tasks: list[dict], *, split: str | None = None,
                       variants: list[dict] | None = None) -> list["Example"]:
    """Turn synthetic repair tasks into training examples.

    The COMPLETION is `child.source_c`, which the record only carries when the builder verified
    it by compiling it and comparing the `.text` section against the target. An unverified task
    is SKIPPED rather than trained on: its label would be a guess.

    `variants` are ADDITIONAL certificate-verified spellings of the same answers, from
    `eval.target_augment`. They exist because compilation is many-to-one: the task's target is an
    equivalence class, `child.source_c` is one member, and training on that member alone penalises a
    model that finds a different one. The certified run measured that happening -- 10 of its 32 exact
    draws were substantively different programs producing the identical object. Each accepted variant
    becomes its own example against the SAME prompt, so the objective sees several accepted
    spellings instead of one.

    Every variant is passed through the same `synthetic_repair_prompt` boundary as the answer, so a
    variant that leaked its answer would be refused here exactly as an answer would be.
    """
    out = []
    by_task: dict[str, list[dict]] = {}
    for row in variants or []:
        by_task.setdefault(row.get("task_id") or "", []).append(row)
    from eval.repair_archive import novelty_key

    for task in tasks:
        if split is not None and task.get("split") != split:
            continue
        if task.get("kind") in LOGIC_KINDS:
            # Compiler-logic tasks (eval/logic_tasks.py) arrive rendered: the prompt was built by the logic_*_prompt
            # functions above and every completion is a compiler verdict (logic_grade.py --self-check grades them).
            # Only the current prompt version is accepted, so a stale export cannot be mixed in.
            if task.get("prompt_version") != LOGIC_PROMPT_VERSION:
                raise ValueError(f"logic task {task.get('id')} has prompt version {task.get('prompt_version')!r}, "
                                 f"not {LOGIC_PROMPT_VERSION!r}: re-export it")
            out.append(Example(kind=task["kind"], prompt=task["prompt"], completion=task["completion"],
                               function=task.get("function") or "", provenance=task.get("provenance") or "",
                               split=task.get("split") or "", record_id=task.get("id") or ""))
            continue
        child = task.get("child") or {}
        if not child.get("exact") or not child.get("source_c"):
            continue
        prompt = synthetic_repair_prompt(
            asm=(task.get("input") or {}).get("assembly") or "",
            candidate=(task.get("input") or {}).get("candidate") or "",
            feedback=((task.get("input") or {}).get("feedback") or {}).get("text") or "")
        common = dict(kind=SYNTHETIC_KIND, prompt=prompt,
                      function=task.get("function") or "",
                      provenance=task.get("source_kind") or "synthetic",
                      split=task.get("split") or "",
                      record_id=task.get("task_id") or "")
        out.append(Example(completion=child["source_c"], **common))
        # ONE EXAMPLE PER NOVELTY CLASS. Collapsing duplicates in the weight map is not enough:
        # an example that is still in the list is still trained on, and a comment-only variant
        # sharing the representative's weight would double that class's contribution. The example
        # list is where "how much of this is genuinely a second approach" is decided.
        seen = {novelty_key(child["source_c"])}
        for row in by_task.get(task.get("task_id") or "", []):
            source = (row.get("source") or "").strip()
            if not source or source == child["source_c"]:
                continue
            key = novelty_key(source)
            if key in seen:
                continue
            seen.add(key)
            out.append(Example(completion=source,
                               provenance=f"{common['provenance']}+verified-spelling:"
                                          f"{row.get('rewrite') or 'unknown'}",
                               **{k: v for k, v in common.items() if k != "provenance"}))
    return out


def load_variants(path) -> list[dict]:
    """Read a `verified-target-variants.jsonl` written by `eval.target_augment`."""
    import json
    from pathlib import Path
    rows = []
    file = Path(path)
    if not file.is_file():
        return rows
    for line in file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows



# The assistant turn is primed with an opening fence. `solver/llm.py` measured partial
# assistant prefills stopping refusals outright (9/9 -> 0/9) and every generation path in the
# project passes one; a trainer that dropped it would train the model to answer a question
# the runtime never asks.
ASSISTANT_PREFILL = "```c\n"


@dataclass(frozen=True)
class Example:
    """One training or evaluation input, with the target completion kept separate."""

    kind: str
    prompt: str
    completion: str
    function: str
    provenance: str = ""
    split: str = ""
    record_id: str = ""
    header_assisted: bool = False

    def digest(self) -> str:
        material = f"{PROMPT_VERSION}\x00{self.kind}\x00{self.prompt}\x00{self.completion}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def leaf_prompt(*, asm: str, draft: str = "", kb: str = "", hints: str = "") -> str:
    """The project's own asm -> C prompt, verbatim."""
    from solver.refine import FIRST_PROMPT
    return FIRST_PROMPT.format(asm=asm, draft=draft, kb=kb, hints=hints)


def repair_prompt(*, target_asm: str, candidate_c: str, compiled: bool,
                  score: float = 0.0, diff: str = "", compiler_stderr: str = "") -> str:
    """The project's own repair prompt, driven by a recorded candidate and its outcome.

    `compiled` selects between the two branches the project already maintains: the compiler's
    error for a candidate that did not build, and the instruction diff for one that did.
    """
    from eval.trajectory_factory import RepairState, render_repair_prompt
    state = RepairState(source=candidate_c, compiled=bool(compiled), score=float(score or 0.0),
                        diff=diff or "", compiler_stderr=compiler_stderr or "")
    return render_repair_prompt(state, target_asm)


def example_from_record(record: dict, *, repo: Path | None = None,
                        include_headers: bool = False,
                        draft: str = "") -> Example | None:
    """Turn one exported source-repair record into an Example.

    Falls back gracefully when a field the record does not carry is needed: a record with no
    assembly cannot make a repair prompt, and returning None is better than substituting the
    m2c draft, because a substituted input is a training example for a different task.
    """
    asm = (record.get("input") or {}).get("target_asm") or ""
    if not asm:
        return None
    outcome = (record.get("input") or {}).get("compiler_outcome") or {}
    prompt = repair_prompt(target_asm=asm,
                           candidate_c=(record.get("input") or {}).get("candidate_c") or "",
                           compiled=bool(outcome.get("compiled")),
                           score=outcome.get("score") or 0.0,
                           diff=outcome.get("diff") or "",
                           compiler_stderr=outcome.get("stderr") or "")
    assisted = False
    if include_headers and repo is not None:
        block = header_block(repo, record.get("function") or "", asm, draft)
        if block:
            prompt = block + "\n" + prompt
            assisted = True
    target = (record.get("target") or {}).get("source_c") or ""
    if not target:
        return None
    return Example(kind=REPAIR_KIND, prompt=prompt, completion=target,
                   function=record.get("function") or "",
                   provenance=record.get("provenance") or "",
                   split=record.get("split") or "",
                   record_id=record.get("id") or "",
                   header_assisted=assisted)


def header_block(repo: Path, function: str, asm: str, draft: str = "") -> str:
    """The project's reconstructed header projection for a function, when one exists.

    Returns "" when no header projection is available. This is reported rather than silently
    omitted: the presence of this block is what makes an example `header-assisted`, and an arm
    that trains with it must be evaluated with it. Removing a header from a prompt does not
    turn a header-derived answer into binary-only evidence -- it only hides where it came from.
    """
    try:
        from solver import project_headers
        block = project_headers.prompt_context(repo, function, asm, draft=draft)
    except Exception:
        return ""
    if not block or block.strip() == "(no project header context found)":
        return ""
    return block


def _ids_of(encoded) -> list[int]:
    """Token ids from whatever `apply_chat_template` / the tokenizer returned.

    transformers 5 returns a `BatchEncoding` for `apply_chat_template(..., tokenize=True)`
    rather than a list. Leniency here is deliberate but not silent: `render_chat` asserts the
    boundary it computes is smaller than the full sequence, so a shape this function
    mis-handles is caught at the call site instead of producing a plausible wrong mask.
    """
    if isinstance(encoded, dict):
        return list(encoded["input_ids"])
    if hasattr(encoded, "input_ids"):
        ids = encoded.input_ids
        return list(ids[0] if ids and isinstance(ids[0], (list, tuple)) else ids)
    return list(encoded)


def render_chat(tokenizer, example: Example, *, system: str = "") -> tuple[list[int], int]:
    """The exact chat token ids and the index where the completion begins.

    Completion-only loss needs the boundary, and the boundary has to come from the SAME
    tokenizer call that produced the ids -- recomputing it separately is how an off-by-N
    trains the model to predict the prompt.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": example.prompt})
    prefix_ids = _ids_of(tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True))
    full_ids = _ids_of(tokenizer.apply_chat_template(
        messages + [{"role": "assistant", "content": example.completion}],
        tokenize=True, add_generation_prompt=False))
    if not 0 < len(prefix_ids) < len(full_ids):
        raise ValueError(
            f"degenerate chat rendering for {example.record_id!r}: "
            f"{len(prefix_ids)} prompt tokens, {len(full_ids)} total. Completion-only loss "
            f"needs a positive boundary strictly inside the sequence.")
    return full_ids, len(prefix_ids)


def describe(example: Example) -> dict:
    return {"id": example.record_id, "kind": example.kind, "function": example.function,
            "provenance": example.provenance, "split": example.split,
            "header_assisted": example.header_assisted,
            "prompt_chars": len(example.prompt), "completion_chars": len(example.completion),
            "digest": example.digest()}


def load_records(path: Path) -> list[dict]:
    import json
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out

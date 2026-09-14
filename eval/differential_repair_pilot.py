#!/usr/bin/env python3
"""Feed causal execution slices to GPT-OSS and verify each edit.

This is a sequential logic-first debugger with an exactness phase transition.
A long-budget diagnosis phase reasons over target and candidate dynamic
provenance, then a separate short phase emits one bounded JSON patch. The
compiler and full differential rerun decide whether the verified prefix moves
forward. Once all finite semantic cases pass, exact object similarity becomes
the primary objective and remains the terminal authority.
"""

from __future__ import annotations

from solver import candidate_frontier, proposal_recovery, control_evidence, residual_sites

import argparse
import hashlib
import json
import re
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

from kb import attempts as attempt_receipts
from solver import (c89, exactness_gradient, llm,
                    mips_differential as differential, modelrepair,
                    principle_variants, project_headers, refine, residual,
                    rewrites, source_layout, semantic_gradient, structgen,
                    transition_policy, workspace)


FUNCTION = "updateRacePlayerMode16AerialTrick"
SCHEMA_VERSION = 2
PROMPT_VERSION = 60
BEHAVIOR_KEY_VERSION = 9
JSON_PREFILL = '{"kind": "'
COMPILER_JSON_PREFILL = (
    '{"kind":"declarations","hypothesis":"fix the compiler error",'
    '"spans":[')
MAX_SOURCE_SPAN_LINES = 40
MAX_SOURCE_SPANS = 12

DIAGNOSIS_PROMPT = """\
You are the investigation phase of a semantic debugger for C reconstructed
from big-endian MIPS and compiled by IDO 5.3. The target and candidate were
executed from identical register and memory states.

Reason as long as necessary to identify the source-level cause of the next
behavioral divergence. Do not emit JSON yet. Use the executed instruction
windows and value provenance rather than guessing from instruction position.
Internal register names may differ harmlessly between programs.
Do not repeat an argument once established. The compiler is deterministic:
before alleging a compiler error, account for C types and scaling. In
particular, adding N to a T* advances N*sizeof(T) bytes; compare that byte
stride with the recorded effective addresses.
Treat CURRENT C member names and offset comments as untrusted hypotheses. The
compiler-computed layout and executed candidate addresses in MECHANICAL
OPCODE-TO-C BRIDGE are facts. A field-name edit that still compiles to the same
wrong byte offset cannot repair an address divergence.
Do not model a non-mutating MIPS load/store displacement with `++pointer` or
`--pointer`: those C operators change the pointer state. `pointer[k]` changes
the access displacement without changing the pointer itself.

Before writing the labeled conclusions, make a short operand ledger for the
next bad observable: exact target opcode(s), exact candidate opcode(s), their
byte widths/strides, and the CURRENT C expression responsible for each
candidate operand. For an address, write the complete raw-byte equation:
base + initialization delta + every executed update + load/store displacement
= observed address. If multiple CURRENT C terms cause that one bad equation,
the minimal plan must change all of those causally coupled terms together.
Stop once one causal source edit is supported; do not fill the token budget by
repeating or reopening contradicted hypotheses.
Your entire response must be at most 900 words. End immediately after the
MINIMAL PATCH PLAN; additional repetition is evidence of failure, not deeper
reasoning.

Your diagnosis must end with these labeled conclusions:
1. NEXT BAD OBSERVABLE
2. TARGET VALUE/ADDRESS PROVENANCE
3. CANDIDATE VALUE/ADDRESS PROVENANCE
4. SOURCE-LEVEL CAUSE
5. MINIMAL PATCH PLAN

In SOURCE-LEVEL CAUSE, quote the exact CURRENT C expression that produces the
wrong target-observable value. In MINIMAL PATCH PLAN, write C only: give the
exact old CURRENT C text and its proposed replacement. Do not give an assembly
patch or merely describe desired MIPS. Translate target loads such as a byte at
`player+0x14` into the smallest supported C field/macro/pointer expression.
The old and replacement C must actually differ. If the proposed replacement
already appears verbatim in CURRENT C, the diagnosis has not found the cause.

The verified prefix is a regression contract for these finite cases, not a
proof over every possible state. Earlier source remains relevant when the
provenance crosses back into it. Do not use or request finished target C.
When execution faults, diagnose the recorded terminal/fault instruction and
its effective-address provenance before considering later missing calls. Exact
project declarations override incompatible declarations invented in CURRENT C.

CURRENT PHASE:
{phase}

TARGET MIPS STATIC CONTEXT:
```
{target_assembly}
```

CURRENT CANDIDATE MIPS STATIC CONTEXT:
```
{candidate_assembly}
```

CURRENT C:
```c
{source}
```

PROJECT HEADER CONTEXT (compiler declarations and layouts, never target C):
```c
{project_context}
```

DYNAMIC CAUSAL EVIDENCE (CROSS-CASE, FORCED-RESYNCHRONIZED):
```
{feedback}
```

MECHANICAL OPCODE-TO-C BRIDGE (controller-derived, not an LLM guess):
```
{mechanical_bridge}
```

GENERIC SOURCE-SHAPE PRINCIPLES:
{principles}

CURRENT COMPILER STATUS:
compiled={compiled}; weighted_progress_score={score:.3f}; exact={exact}

CURRENT EXACT ASSEMBLY RESIDUAL (`-` target, `+` candidate):
```
{diff}
```

GENERIC SOURCE-SHAPE PRINCIPLES DERIVED FROM THAT RESIDUAL:
{principles}

VERIFIED TRAJECTORY CONTRACTS AND REJECTED OBSERVATIONS:
{rejected}
"""


EXACTNESS_DIAGNOSIS_PROMPT = """\
You are the investigation phase of a byte-exact C reconstruction loop for
big-endian MIPS compiled by IDO 5.3.

CURRENT PHASE:
{phase}

There is NO observed behavioral divergence to diagnose in the supplied
target-observable executions. Do not invent one. Cases where the target itself
hit the execution limit are coverage debt, not evidence against the candidate.
The task in this phase is to explain how semantically preserving C source shape
can make the deterministic compiler emit the remaining target instruction
shape.

Physical MIPS register names are compiler allocation results. A C local named
`t5` does not request physical register t5, and a target/candidate t5-versus-t3
difference does not imply a pointer offset or value error. The controller has
already classified and compressed the residual. When its full-stream class is
register-operand-only or register/local-order-only, do NOT reparse or narrate
the raw diff, and do not confuse an o32 entry register with a similarly named
C local. Treat the deterministic feed as authoritative mechanical evidence.
Only a MIXED STATIC RESIDUAL requires inspecting the supplied raw context for
an opcode, immediate, displacement, symbol, width, or signedness mismatch.

For a register-operand-only or register/local-order-only residual, reason about live ranges and scheduling:
declaration/initializer placement, scope boundaries, independent statement
order, materializing or inlining a temporary, and commutative operand order.
Any proposal must preserve values, addresses, pointer strides, branch outcomes,
memory operations, calls, and return values. Do not change numeric constants,
pointer offsets or array indices, comparison conditions, loads/stores, or data
types unless the exact residual contains a corresponding non-register opcode,
immediate, displacement, or signedness difference.

Start immediately with the five labeled conclusions below, with no analysis
preamble. Choose the first still-untried experiment supported by the feed and
turn it into one bounded C experiment. Do not emit JSON. Do not use or request
finished target C. The response must be at most 450 words.

Your diagnosis must end with these labeled conclusions:
1. RESIDUAL CLASSIFICATION
2. TARGET REGISTER/INSTRUCTION ROLES
3. CANDIDATE C LIVE RANGES AND SOURCE ORDER
4. SEMANTICS-PRESERVING SOURCE-SHAPE CAUSE
5. MINIMAL PATCH PLAN

In MINIMAL PATCH PLAN, give exact old CURRENT C text and replacement C text.
They must differ. The replacement must implement only the stated
semantics-preserving source-shape experiment, never an assembly patch.

CONTROLLER RESIDUAL CLASSIFICATION:
{residual_classification}

CONTROLLER-DETERMINISTIC EXACTNESS FEED:
```
{exactness_feed}
```

STATIC RESIDUAL CONTEXT:
{static_residual_context}

CURRENT C:
```c
{source}
```

GENERIC SOURCE-SHAPE PRINCIPLES DERIVED FROM THAT RESIDUAL:
{principles}

TARGET-OBSERVABLE EXECUTION CONTRACTS:
```
{feedback}
```

PROJECT HEADER CONTEXT (compiler declarations and layouts, never target C):
```c
{project_context}
```

CURRENT COMPILER STATUS:
compiled={compiled}; weighted_progress_score={score:.3f}; exact={exact}

VERIFIED TRAJECTORY CONTRACTS AND REJECTED OBSERVATIONS:
{rejected}
"""


PATCH_PROMPT = """\
You are the patch-emission phase of a semantic debugger for C reconstructed
from big-endian MIPS and compiled by IDO 5.3. Convert the investigator's
diagnosis into ONE bounded source patch.

Return JSON only:
{{
  "kind": "one of: {kinds}",
  "hypothesis": "brief causal explanation",
  "spans": [{{"start_line": 12, "end_line": 13, "new": "replacement C"}}]
}}

Rules:
- Return one to twelve non-overlapping source spans, changing at most
  {max_span_lines} source lines in total.
- Emit executable C changes only. Do not add explanatory comments or leave the
  diagnosed bad expression in place.
- Line numbers refer to the numbered CURRENT C below; the `NN |` prefixes are
  annotations and must not appear in `new`.
- `new` replaces every complete line from `start_line` through `end_line`.
  Do not return a whole file.
{objective}
- Apply the diagnosis's exact C old-to-new replacement directly. Do not
  reinterpret a concrete field-load diagnosis as an unrelated flag/timer edit.
- If one diagnosed address equation contains multiple wrong CURRENT C terms,
  emit every coupled correction together in this one multi-span patch.
- A span replaces complete source lines. When a selected line contains several
  semicolon-separated statements, retain every unrelated sibling statement;
  change only the diagnosed statement within that line.
- The AUTHORITATIVE operand ledger locks operands marked EXACT. If ADDRESS and
  WIDTH are exact but VALUE differs, changing a pointer-store lvalue, pointer
  update, or pointee type is a validation error; edit the value producer or
  the predicate that selected it.
- Keep `hypothesis` concise; the full reasoning is already recorded separately.
- You may correct a macro, field offset, declaration, or function body when the
  diagnosis and execution evidence support it.
- Do not use or request finished/reference target C.
- No assembly in C, GLOBAL_ASM, INCLUDE_ASM, new includes, or pragmas.
- Preserve C89 and the externally visible function signature.

NUMBERED CURRENT C:
```c
{source}
```

CONTROLLER-DETERMINISTIC EXACTNESS FEED (authoritative when it conflicts with
the investigator's prose):
```
{exactness_feed}
```

INVESTIGATOR DIAGNOSIS (possibly incomplete; mechanical facts override it):
```
{diagnosis}
```

DYNAMIC CAUSAL EVIDENCE (CROSS-CASE, FORCED-RESYNCHRONIZED):
```
{feedback}
```

AUTHORITATIVE MECHANICAL OPCODE-TO-C BRIDGE:
```
{mechanical_bridge}
```

EXACT STATIC SOURCE-SHAPE PRINCIPLES:
{principles}

VERIFIED TRAJECTORY CONTRACTS AND REJECTED OBSERVATIONS:
{rejected}

FINAL PATCH OBLIGATION:
For an address repair, recompute the complete CURRENT C raw-byte equation after
your proposed spans. If the target observed base+T and the edited expression
still sums to anything other than base+T on that executed path, do not emit it.
Mechanical facts above override an incomplete or contradictory diagnosis.
"""


EXACTNESS_PATCH_PROMPT = """\
You are the source-shape actuation phase of a byte-exact C reconstruction loop
for big-endian MIPS compiled by IDO 5.3. The controller has mechanically
proved that the complete remaining instruction streams differ only in
physical register allocation or local instruction order. There is no observed
target-side value, address, pointer-stride, branch, call, or memory-operation
error to repair.

Your only task is to select ONE still-untried experiment from the deterministic
feed and express it as a bounded C edit. Prefer the first listed experiment
that can be represented safely in CURRENT C. This is an experiment: the
compiler and semantic replay, not your prose, will decide whether it works.

Return JSON only:
{{
  "kind": "declarations",
  "hypothesis": "brief source-shape experiment",
  "spans": [{{"start_line": 12, "end_line": 13, "new": "replacement C"}}]
}}

Rules:
- Return one to twelve non-overlapping complete-line spans changing at most
  {max_span_lines} source lines total. The `NN |` prefixes are annotations.
- Valid experiment kinds are `declarations`, `temporaries`, `control-flow`,
  or `expression`; do not use `layout` or `type-width` for this residual.
- Preserve every numeric/string/character literal, pointer offset, array
  index, comparison, load/store, function call, and externally visible type.
- Never change `dst + 1` to `dst + 2`: `dst` is a `u16 *`, so `+ 1` already
  means the target's two-byte address increment.
- Do not rename a C local merely to resemble a physical register. The entry
  ABI map in the feed identifies what physical `a0`-`a3` mean.
- Do not repeat a compiler-experiment family whose feed says all attempts
  compiled and none were accepted.
- A value-epoch split needs at least two edits: add the new local beside the
  feed's declaration line, then rename the selected epoch's definition and
  every required use. Never replace an executable assignment with a
  declaration, and never emit an unused new local.
- Do not emit assembly, comments, includes, pragmas, or a whole file.

CONTROLLER-DETERMINISTIC EXACTNESS FEED (authoritative):
```
{exactness_feed}
```

NUMBERED CURRENT C:
```c
{source}
```

FINAL CHECK BEFORE JSON: the edit must leave every forbidden semantic token
above unchanged and must enact one untried lifetime/scope/value-epoch/temp/CFG
shape experiment from the feed. A pointer-stride correction is mechanically
contradicted and invalid.
"""


PATCH_RETRY_PROMPT = """\
Your previous patch could not be applied to CURRENT C.

VALIDATION ERROR:
{error}

PREVIOUS RESPONSE:
```
{response}
```

Return corrected JSON only in exactly this shape:
{{
  "kind": "expression",
  "hypothesis": "brief cause",
  "spans": [{{"start_line": 12, "end_line": 12, "new": "different C"}}]
}}

Every `spans` item must be an object containing all three fields shown above;
do not return two-item arrays or an empty list. Choose one to twelve valid,
non-overlapping line intervals changing at most {max_span_lines} source lines
in total. `new` must differ semantically from the selected complete source
lines and must contain executable C, not new explanatory comments. If the
validation error says no-op, the earlier patch merely repeated CURRENT C: use
the diagnosis and causal evidence below to revise the actual old-to-new edit.
Do not edit assembly text. The `NN |` prefixes are annotations, not source.

INVESTIGATOR DIAGNOSIS (possibly incomplete):
```
{diagnosis}
```

CAUSAL EVIDENCE:
```
{feedback}
```

AUTHORITATIVE MECHANICAL OPCODE-TO-C BRIDGE:
```
{mechanical_bridge}
```

CONTROLLER-DETERMINISTIC EXACTNESS FEED:
```
{exactness_feed}
```

NUMBERED CURRENT C:
```c
{source}
```

The mechanical bridge overrides an incomplete diagnosis. If it derives a
bounded C expression that satisfies the exact byte equation, apply that
expression instead of repeating a no-op or a contradicted earlier edit.
"""


EXACTNESS_PATCH_RETRY_PROMPT = """\
The previous byte-exact source-shape experiment was rejected before compile.

VALIDATION ERROR:
{error}

CORRECTION ACTION:
{retry_action}

Return one JSON object only. It must have a `kind` string, a concise
`hypothesis` string, and a `spans` array. Every array item must be an object
with integer `start_line`, integer `end_line`, and string `new` fields. Derive
all line numbers and replacement C from the feed and NUMBERED CURRENT C; no
example edit or placeholder is supplied for you to copy.

The complete residual is physical-register/local-order-only. Preserve every
numeric/string/character literal, pointer offset, array index, comparison,
load/store, call, and externally visible type. Never change `dst + 1` to
`dst + 2`; typed pointer scaling already makes `+ 1` a two-byte increment.
Use one to twelve complete-line spans and change at most {max_span_lines}
source lines. The `NN |` prefixes are annotations, not source. Emit no prose
outside the JSON.
For a value-epoch split, add its declaration beside the feed's declaration
line AND rename the selected epoch's definition and required uses. Do not
replace an executable assignment with a declaration or create an unused local.
A value-epoch split containing only one declaration span is invalid.

CONTROLLER-DETERMINISTIC EXACTNESS FEED (authoritative):
```
{exactness_feed}
```

NUMBERED CURRENT C:
```c
{source}
```
"""


COMPILER_FIX_PROMPT = """\
A semantically motivated C patch failed to compile under IDO 5.3. Fix only the
compiler blocker while preserving the intended semantic change. Return JSON
only with one to twelve objects in this exact bounded-span shape:
`"spans":[{{"start_line":12,"end_line":12,"new":"different compiling C"}}]`.
Every object needs all three fields. `new` must differ from the currently
noncompiling source; repeating its lines is a no-op, not a fix. Use at most
{max_span_lines} complete numbered source lines total. The compiler line number
has been reset to match the numbered C below. The `NN |` prefixes are
annotations, not source text. Do not edit assembly text, add includes, add
explanatory comments, or use inline assembly.
You must emit an edit, not an error report or another diagnosis. To delete a
stray line N, use `{{"start_line":N,"end_line":N,"new":""}}`.

SEMANTIC DIAGNOSIS:
```
{diagnosis}
```

COMPILER ERROR:
```
{compiler_error}
```

NUMBERED CURRENT NONCOMPILING C:
```c
{source}
```
"""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _binary_observed_offsets(conn: sqlite3.Connection,
                             function: str) -> dict[int, int]:
    """Flatten this function's parameter-memory facts for safe repadding."""
    observed: dict[int, int] = {}
    for entries in structgen.layout(conn, function).values():
        for offset, width, _ctype in entries:
            observed[offset] = max(width, observed.get(offset, 0))
    return observed


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    for retry in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            # Windows readers can briefly deny rename across the WSL mount.
            # Keep the completed temporary receipt and retry the atomic commit.
            if retry == 5:
                raise
            time.sleep(0.1 * (retry + 1))


def _causal_feedback(
        results: list[differential.DifferentialResult]) -> str:
    return "\n\n".join(differential.causal_feedback(row) for row in results)


def _target_inconclusive(row: differential.DifferentialResult) -> bool:
    """The test cannot judge the candidate because the target did not finish."""
    return row.status == "inconclusive" and row.target.status != "returned"


def _observed_semantics_clean(
        results: list[differential.DifferentialResult]) -> bool:
    """Every target-observable case passes; target-limited cases are debt.

    This is deliberately weaker than full semantic settlement.  It authorizes
    semantics-preserving exactness experiments and score-based search, but the
    final receipt continues to report incomplete coverage until every case is
    conclusive.  A candidate timeout against a returning target remains a
    failure of this predicate.
    """
    observable = [row for row in results if not _target_inconclusive(row)]
    return bool(observable) and all(row.status == "passed" for row in observable)


def _diagnostic_cases(
        cases: tuple[differential.TestCase, ...],
        results: list[differential.DifferentialResult], *,
        maximum: int = 8) -> tuple[differential.TestCase, ...]:
    """Choose path/value-diverse failures for expensive prompt evidence."""
    by_name = {case.name: case for case in cases}
    selected: list[differential.TestCase] = []
    selected_names: set[str] = set()
    signatures: set[tuple] = set()
    failing = [row for row in results if row.status == "failed"]
    for row in failing:
        branches = tuple(
            (event.instruction, event.effect.startswith("branch taken;"))
            for event in row.target.trace if event.effect.startswith("branch "))
        mismatch = semantic_gradient.value_mismatches_from_result(row)
        value_shape = tuple(
            (item.kind, item.target.signature(), item.candidate.signature())
            for item in mismatch[:1])
        signature = (branches, value_shape)
        if signature in signatures or row.case not in by_name:
            continue
        signatures.add(signature)
        selected.append(by_name[row.case])
        selected_names.add(row.case)
        if len(selected) >= maximum:
            return tuple(selected)
    for row in failing:
        if row.case in by_name and row.case not in selected_names:
            selected.append(by_name[row.case])
            selected_names.add(row.case)
            if len(selected) >= maximum:
                break
    if selected:
        return tuple(selected)
    # When no semantic failure exists, do not feed target-side step-limit
    # traces to the investigator. They contain no candidate divergence, can be
    # tens of thousands of events long, and previously made a register-only
    # residual look like a pointer bug. Passing cases still provide a compact
    # contract for exactness work.
    observable = [row for row in results if not _target_inconclusive(row)]
    for row in observable:
        if row.case in by_name and row.case not in selected_names:
            selected.append(by_name[row.case])
            selected_names.add(row.case)
            if len(selected) >= maximum:
                break
    return tuple(selected)


_POINTER_STORE_LINE = re.compile(
    r"(?:\*\s*[A-Za-z_]\w*|[A-Za-z_]\w*\s*\[[^\]]+\])\s*=")


def _write_opcode(run: differential.RunResult,
                  write: differential.WriteEvent) -> str:
    if 0 <= write.trace_position < len(run.trace):
        return run.trace[write.trace_position].text
    for event in reversed(run.trace):
        if event.instruction == write.instruction and \
                re.match(r"\s*(?:sb|sh|sw)\b", event.text):
            return event.text
    return "(store opcode unavailable)"


def _selector_branch(run: differential.RunResult,
                     write: differential.WriteEvent) -> str:
    end = min(write.trace_position, len(run.trace))
    for event in reversed(run.trace[:end]):
        if not event.effect.startswith("branch "):
            continue
        reads = ", ".join(
            f"{name}={value:#x} from {provenance}"
            for name, value, provenance in event.reads)
        return (f"{event.text} | {event.effect}" +
                (f" | operands: {reads}" if reads else ""))
    return "(no preceding conditional branch recorded)"


def _byte_compare_window(run: differential.RunResult,
                         write: differential.WriteEvent) -> list[str]:
    end = min(write.trace_position, len(run.trace))
    for position in range(end - 1, -1, -1):
        event = run.trace[position]
        if not event.effect.startswith("branch ") or len(event.reads) < 2:
            continue
        load_operands = sum(bool(re.search(
            r"\b(?:lb|lbu)\b", provenance))
            for _name, _value, provenance in event.reads)
        if load_operands < 2:
            continue
        window = run.trace[max(0, position - 3):min(end, position + 5)]
        rendered = []
        for item in window:
            outputs = ", ".join(
                f"{name}={value:#x}" for name, value, _origin in item.writes)
            rendered.append(
                f"  {item.text}" +
                (f" | {item.effect}" if item.effect else "") +
                (f" | out {outputs}" if outputs else ""))
        return rendered
    return ["  (no executed byte-comparison branch recognized)"]


def _current_output_slice(source: str, limit: int = 24) -> list[str]:
    """Render a bounded lexical C slice around possible persistent stores."""
    lines = source.splitlines()
    output_indexes = [
        index for index, line in enumerate(lines)
        if _POINTER_STORE_LINE.search(line.split("//", 1)[0])
    ]
    if not output_indexes:
        return ["  (no simple pointer-store expression recognized)"]
    selected = set(output_indexes)
    identifiers: set[str] = set()
    for index in output_indexes:
        identifiers.update(re.findall(r"\b[A-Za-z_]\w*\b", lines[index]))
        for nearby in range(max(0, index - 5), index):
            if re.search(r"\b(?:if|else)\b", lines[nearby]):
                selected.add(nearby)
                identifiers.update(
                    re.findall(r"\b[A-Za-z_]\w*\b", lines[nearby]))
    ignored = {
        "if", "else", "char", "short", "int", "long", "signed",
        "unsigned", "void", "const", "volatile", "s8", "u8", "s16",
        "u16", "s32", "u32", "s64", "u64", "f32",
    }
    identifiers -= ignored
    for _depth in range(4):
        before = len(identifiers)
        for index, line in enumerate(lines):
            code = line.split("//", 1)[0]
            if any(re.search(
                    rf"\b{re.escape(name)}\s*(?:=|\+=|-=|\+\+|--)", code)
                   for name in identifiers):
                selected.add(index)
                identifiers.update(re.findall(r"\b[A-Za-z_]\w*\b", code))
                identifiers -= ignored
        if len(identifiers) == before:
            break
    for index in tuple(selected):
        for nearby in range(max(0, index - 2), min(len(lines), index + 3)):
            if re.search(r"\b(?:if|else|for|while)\b", lines[nearby]):
                selected.add(nearby)
    return [f"  C line {index + 1}: {lines[index].strip()}"
            for index in sorted(selected)[:limit]]


def _encoded_store_shape(source: str) -> tuple[int, str, int, str, str] | None:
    for number, line in enumerate(source.splitlines(), 1):
        if not _POINTER_STORE_LINE.search(line.split("//", 1)[0]):
            continue
        shape = re.search(
            r"(?P<high>[A-Za-z_]\w*)\s*<<\s*(?P<shift>[0-9]+)"
            r"[^;|]*\|\s*(?P<low>[A-Za-z_]\w*)", line)
        if shape is not None:
            return (number, shape.group("high"), int(shape.group("shift")),
                    shape.group("low"), line.strip())
    return None


def _counter_role_bridge(
        results: list[differential.DifferentialResult],
        source: str) -> list[str]:
    """Bind an executed pre-compare counter to a separate success counter."""
    encoded = _encoded_store_shape(source)
    if encoded is None:
        return []
    _store_line, high, _shift, _low, _expression = encoded
    precompare = re.search(
        r"(?P<attempt>[A-Za-z_]\w*)\s*\+\+\s*;\s*"
        r"if\s*\(\s*\*(?P<left>[A-Za-z_]\w*)\s*!=\s*"
        r"\*(?P<right>[A-Za-z_]\w*)\s*\)\s*break\s*;", source)
    if precompare is None:
        return []
    attempt = precompare.group("attempt")
    best = re.search(
        rf"if\s*\(\s*{re.escape(high)}\s*<\s*{re.escape(attempt)}\s*\)"
        rf"\s*\{{[^}}]*\b{re.escape(high)}\s*=\s*"
        rf"{re.escape(attempt)}\s*;", source, re.S)
    if best is None:
        return []

    target_opcodes = {
        re.sub(r"\s+", " ", event.text.strip())
        for row in results for event in row.target.trace
    }
    success = None
    for opcode in target_opcodes:
        move = re.fullmatch(
            rf"move {re.escape(high)},(?P<success>[A-Za-z_]\w*)", opcode)
        if move is not None and move.group("success") != attempt:
            name = move.group("success")
            if f"addiu {name},{name},1" in target_opcodes and \
                    f"move {name},zero" in target_opcodes:
                success = name
                break
    if success is None:
        return []

    lines = source.splitlines()
    line_of = lambda offset: source.count("\n", 0, offset) + 1
    pre_line = line_of(precompare.start())
    best_line = line_of(best.start())
    pointer_line = next((
        number for number, line in enumerate(lines, 1)
        if f"{precompare.group('left')}++" in line and
        f"{precompare.group('right')}++" in line), None)
    limit_update_line = next((
        number for number, line in enumerate(lines, 1)
        if re.search(r"\ba0\s*--", line)), None)
    negative_line = next((
        number for number, line in enumerate(lines, 1)
        if re.search(r"if\s*\(\s*a2\s*<\s*0\s*\)", line)), None)
    rendered = [
        "CONTROLLER-DERIVED COUNTER-ROLE TRANSLATION (from executed opcodes):",
        (f"- CURRENT C line {pre_line} increments `{attempt}` before testing "
         "byte inequality, so it counts attempted comparisons, including the "
         "mismatching byte."),
        (f"- Target executes `move {success},zero`, increments it with `addiu "
         f"{success},{success},1` only on the equality fall-through path, and "
         f"uses `move {high},{success}` for the best/output length. Thus "
         f"`{success}` is the successful-byte count and `{attempt}` is only the "
         "loop-attempt count."),
        (f"- CURRENT C line {best_line} incorrectly copies `{attempt}` into "
         f"encoded length `{high}`. The opcode-supported source experiment is "
         f"to compare/assign `{high}` from `{success}` instead."),
    ]
    if negative_line is not None:
        rendered.append(
            f"- Reset `{success} = 0;` immediately before CURRENT C line "
            f"{negative_line}, matching target `move {success},zero` for each "
            "candidate match position.")
    if pointer_line is not None:
        old = lines[pointer_line - 1].strip()
        rendered.append(
            f"- CURRENT C line {pointer_line} `{old}` is the equality "
            f"fall-through update: preserve both pointer increments, replace "
            f"the limit mutation with `{success}++;`. The compare limit must "
            f"remain fixed for `{attempt}`'s loop-bound test.")
    if limit_update_line is not None and limit_update_line != pointer_line:
        rendered.append(
            f"- Remove the limit mutation at CURRENT C line "
            f"{limit_update_line}; the target compares `{attempt}` against a "
            "fixed limit.")
    return rendered


def _fixed_compare_limit_bridge(
        results: list[differential.DifferentialResult],
        source: str) -> list[str]:
    """Expose a C loop bound mutated where the target keeps it fixed."""
    bound_test = re.search(
        r"if\s*\(\s*(?P<count>[A-Za-z_]\w*)\s*==\s*"
        r"(?P<limit>[A-Za-z_]\w*)\s*\)\s*break\s*;", source)
    if bound_test is None:
        return []
    count, limit = bound_test.group("count"), bound_test.group("limit")
    mutation = re.search(rf"\b{re.escape(limit)}\s*--\s*;", source)
    if mutation is None:
        return []
    target_opcodes = {
        re.sub(r"\s+", " ", event.text.strip())
        for row in results for event in row.target.trace
    }
    candidate_opcodes = {
        re.sub(r"\s+", " ", event.text.strip())
        for row in results for event in row.candidate.trace
    }
    target_compare = next((
        opcode for opcode in target_opcodes
        if re.fullmatch(
            rf"bne {re.escape(count)},{re.escape(limit)},.+", opcode)), None)
    candidate_mutation = next((
        opcode for opcode in candidate_opcodes
        if opcode == f"addiu {limit},{limit},-0x1" or
        opcode == f"addiu {limit},{limit},-1"), None)
    if target_compare is None or candidate_mutation is None:
        return []
    line = source.count("\n", 0, mutation.start()) + 1
    old_line = source.splitlines()[line - 1].strip()
    new_line = re.sub(
        rf"\s*{re.escape(limit)}\s*--\s*;", "", old_line).rstrip()
    return [
        "CONTROLLER-DERIVED FIXED-LIMIT TRANSLATION (from executed opcodes):",
        (f"- Target executes `{target_compare}` after incrementing `{count}`; "
         f"it does not decrement `{limit}` on that equality path."),
        (f"- Candidate executes `{candidate_mutation}` because CURRENT C line "
         f"{line} contains `{limit}--;`. This shrinks the limit while `{count}` "
         "grows and terminates a multi-byte match early."),
        (f"- Opcode-supported bounded edit: replace CURRENT C line {line} "
         f"`{old_line}` with `{new_line}`. Preserve all sibling pointer and "
         "successful-count updates."),
    ]


def next_bad_observable_ledger(
        results: list[differential.DifferentialResult], source: str) -> str:
    """Put the first bad opcode operands ahead of the long causal trace."""
    selected = None
    for row in results:
        if _target_inconclusive(row):
            continue
        index = _write_prefix(row)
        if index < len(row.target.writes) and \
                index < len(row.candidate.writes):
            selected = (row, index)
            break
    lines = [
        "AUTHORITATIVE NEXT-OBSERVABLE OPERAND LEDGER",
        ("This compact controller summary takes precedence over hypotheses "
         "formed from the longer trace below."),
    ]
    if selected is None:
        failed = next((row for row in results if row.status == "failed"), None)
        if failed is None:
            inconclusive = sum(_target_inconclusive(row) for row in results)
            lines.append(
                "All target-observable semantic cases pass; no bad write or "
                "call divergence exists.")
            if inconclusive:
                lines.append(
                    f"{inconclusive} additional case(s) are coverage debt "
                    "because the TARGET hit the execution limit. They are not "
                    "evidence of a candidate semantic bug.")
            lines.append(
                "The remaining static residual is compiler/source shape. "
                "Physical MIPS register names are allocation results; do not "
                "change pointer values, strides, or data merely to make a C "
                "local's spelling resemble a target register name.")
        else:
            lines.extend([
                f"case: {failed.case}",
                f"next observable: {failed.first_divergence}",
                ("No aligned target/candidate write pair is available; reason "
                 "from the recorded call or terminal-status operands."),
            ])
        return "\n".join(lines)

    row, index = selected
    target = row.target.writes[index]
    candidate = row.candidate.writes[index]
    address_exact = target.address == candidate.address
    width_exact = target.width == candidate.width
    value_exact = target.value == candidate.value
    target_dag = semantic_gradient.value_dag(row.target, target).summary()
    candidate_dag = semantic_gradient.value_dag(
        row.candidate, candidate).summary()
    exact_operands = [
        name for name, exact in (
            ("address", address_exact), ("width", width_exact),
            ("value", value_exact)) if exact]
    wrong_operands = [
        name for name, exact in (
            ("address", address_exact), ("width", width_exact),
            ("value", value_exact)) if not exact]
    lines.extend([
        f"case: {row.case}; first unequal write ordinal: {index}",
        f"target opcode: {_write_opcode(row.target, target)}",
        f"candidate opcode: {_write_opcode(row.candidate, candidate)}",
        (f"ADDRESS {'EXACT' if address_exact else 'DIFFERS'}: target "
         f"{target.address}; candidate {candidate.address}"),
        f"target address equation/provenance: {target.address_provenance}",
        ("candidate address equation/provenance: "
         f"{candidate.address_provenance}"),
        (f"WIDTH {'EXACT' if width_exact else 'DIFFERS'}: target "
         f"{target.width} byte(s); candidate {candidate.width} byte(s)"),
        (f"VALUE {'EXACT' if value_exact else 'DIFFERS'}: target "
         f"{target.value:#x}; candidate {candidate.value:#x}"),
        f"target executed value producer: {target_dag}",
        f"candidate executed value producer: {candidate_dag}",
        ("target last selector branch before store: "
         f"{_selector_branch(row.target, target)}"),
        ("candidate last selector branch before store: "
         f"{_selector_branch(row.candidate, candidate)}"),
        f"target value provenance: {target.value_provenance}",
        f"candidate value provenance: {candidate.value_provenance}",
        ("LOCKED OPERANDS: " + ", ".join(exact_operands) +
         (". Preserve the byte address and value. A field-width correction "
          "must NOT remove preceding padding or relocate the field. Width "
          f"is not displacement: the {target.width}-byte store must still start "
          f"at {target.address}."
          if address_exact and not width_exact else
          ". Do not edit their C pointer/type machinery.")
         if exact_operands else "LOCKED OPERANDS: none."),
        ("REPAIR SCOPE: " + ", ".join(wrong_operands) +
         ". Trace only these producer operands and any predicate that selected "
         "the wrong producer."),
        "TARGET EXECUTED BYTE-COMPARISON WINDOW BEFORE THIS OUTPUT:",
        *_byte_compare_window(row.target, target),
        "CANDIDATE EXECUTED BYTE-COMPARISON WINDOW BEFORE THIS OUTPUT:",
        *_byte_compare_window(row.candidate, candidate),
        ("COUNTER-ROLE RULE: an increment executed before the byte inequality "
         "branch counts attempted comparisons. A length increment reachable "
         "only after equality counts successful bytes. The value encoded in "
         "the output length field must come from the successful-byte role."),
        *_counter_role_bridge(results, source),
        *_fixed_compare_limit_bridge(results, source),
    ])

    aligned = []
    for sample in results:
        sample_index = _write_prefix(sample)
        if sample_index < len(sample.target.writes) and \
                sample_index < len(sample.candidate.writes):
            aligned.append((sample, sample.target.writes[sample_index],
                            sample.candidate.writes[sample_index]))
    if aligned:
        lines.append("CROSS-CASE FIRST-BAD-WRITE TABLE (do not hard-code one case):")
        for sample, target_write, candidate_write in aligned[:8]:
            lines.append(
                f"  {sample.case}: target={target_write.value:#x} from "
                f"{semantic_gradient.value_dag(sample.target, target_write).summary()}; "
                f"candidate={candidate_write.value:#x} from "
                f"{semantic_gradient.value_dag(sample.candidate, candidate_write).summary()}")

    encoded = _encoded_store_shape(source)
    if encoded is not None and aligned:
        number, high, shift, low, expression = encoded
        deltas = [
            (candidate_write.value >> shift) - (target_write.value >> shift)
            for _sample, target_write, candidate_write in aligned
        ]
        lines.append(
            f"CURRENT C encoded-store correlation: line {number} `{expression}` "
            f"places `{high}` in bits {shift}+, with `{low}` below it.")
        if len(set(deltas)) == 1 and deltas[0] != 0:
            relation = f"{deltas[0]:+d}"
            lines.append(
                f"CONTROLLER-DERIVED CROSS-CASE RELATION: candidate high field "
                f"`{high}` is target {relation} in all {len(deltas)} aligned "
                f"failing case(s). Repair `{high}`'s successful-match count or "
                "the predicate selecting this encoded store; do not hard-code "
                "any observed value.")
        if candidate_dag.startswith(("or(", "sll(")) and \
                target_dag.startswith(("lbu ", "lb ", "lhu ", "lh ")):
            lines.append(
                "CAUSAL EXCLUSION: the candidate bad store executed the encoded "
                f"`{high} << {shift} | {low}` producer, not the alternate "
                "literal-load producer. Editing that literal load cannot change "
                "this executed bad value; repair the count/selection upstream.")
    lines.extend([
        "CURRENT C output/control/definition slice (lexical candidates):",
        *_current_output_slice(source),
    ])
    return "\n".join(lines)


def _operation_feedback(
        results: list[differential.DifferentialResult], source: str,
        divergence_bundle: str) -> str:
    return (control_evidence.render(results) + "\n\n" +
            next_bad_observable_ledger(results, source) + "\n\n" +
            divergence_bundle + "\n\n" +
            semantic_gradient.render_operation_gradient(results, source))


def _prioritized_feedback(results, source, divergence_bundle=""):
    contrast = control_evidence.render(results)
    ledger = next_bad_observable_ledger(results, source)
    remaining = divergence_bundle or _causal_feedback(results)
    for part in (contrast, ledger):
        if part:
            remaining = remaining.replace(part, "", 1)
    return "\n\n".join(part for part in (contrast, ledger, remaining.strip()) if part)


_C_POINTER_DECL = re.compile(
    r"\b(?P<type>(?:(?:const|volatile|signed|unsigned)\s+)*"
    r"(?:char|short|int|long|float|double|[us](?:8|16|32|64)|f32))"
    r"\s*\*\s*(?P<name>[A-Za-z_]\w*)\b")
_C_SCALAR_BYTES = {
    "char": 1, "signed char": 1, "unsigned char": 1,
    "s8": 1, "u8": 1,
    "short": 2, "signed short": 2, "unsigned short": 2,
    "s16": 2, "u16": 2,
    "int": 4, "signed int": 4, "unsigned int": 4,
    "long": 4, "signed long": 4, "unsigned long": 4,
    "s32": 4, "u32": 4, "f32": 4, "float": 4,
    "s64": 8, "u64": 8, "double": 8,
}
_INTEGER_LITERAL = r"(?:0x[0-9A-Fa-f]+|[0-9]+)"
_DYNAMIC_OPCODE = re.compile(
    r"dyn#\d+/i\d+\s+(?P<opcode>[a-z][a-z0-9.]*)"
    r"(?:\s+(?P<args>[^|\r\n]+))?")


def _c_address_and_layout_facts(source: str, limit: int = 24) -> list[str]:
    """Mechanically spell out C facts models commonly misread as byte adds.

    This is intentionally a small source-local audit, not a decompiler.  It
    makes constant pointer scaling and compiler-computed partial layouts
    explicit so an investigator cannot explain observed MIPS with the layout
    comments that happened to accompany an LLM draft.
    """
    pointer_types: dict[str, tuple[str, int]] = {}
    for match in _C_POINTER_DECL.finditer(source):
        type_name = " ".join(re.sub(
            r"\b(?:const|volatile)\b", "", match.group("type")).split())
        size = _C_SCALAR_BYTES.get(type_name)
        if size is not None:
            pointer_types[match.group("name")] = (type_name, size)

    facts: list[str] = []
    for line_number, raw_line in enumerate(source.splitlines(), 1):
        code = raw_line.split("//", 1)[0]
        code = re.sub(r"/\*.*?\*/", "", code)
        for name, (type_name, size) in pointer_types.items():
            for item in re.finditer(rf"\*\s*--\s*{name}\s*=", code):
                facts.append(
                    f"C line {line_number}: `{item.group(0).rstrip('= ').strip()}` "
                    f"first mutates `{name}` by {-size:+d} bytes, then stores at "
                    "displacement +0 from the changed pointer. A MIPS store "
                    f"such as `sh value,{-size}({name})` does not mutate "
                    f"`{name}`; `{name}[-1]` supplies the same {-size:+d}-byte "
                    "access displacement without the pointer side effect.")
            operations = (
                (rf"\b{name}\s*=\s*\([^()]*\*\)\s*\(\s*"
                 rf"\(\s*char\s*\*\s*\)\s*{name}\s*\+\s*"
                 rf"(?P<count>{_INTEGER_LITERAL})", "raw-byte increment"),
                (rf"\b{name}\s*=\s*[A-Za-z_]\w*\s*\+\s*"
                 rf"(?P<count>{_INTEGER_LITERAL})", "assignment"),
                (rf"\b{name}\s*\+=\s*(?P<count>{_INTEGER_LITERAL})",
                 "increment"),
                (rf"\b{name}\s*-=\s*(?P<count>{_INTEGER_LITERAL})",
                 "decrement"),
            )
            for pattern, operation in operations:
                for item in re.finditer(pattern, code):
                    count = int(item.group("count"), 0)
                    sign = -1 if operation == "decrement" else 1
                    scale = 1 if operation == "raw-byte increment" else size
                    expression = item.group(0).strip()
                    facts.append(
                        f"C line {line_number}: `{expression}`; `{name}` is "
                        f"`{type_name} *`, so the raw address delta is "
                        f"{sign * count * scale:+d} byte(s)" +
                        (" because the arithmetic is explicitly cast through "
                         "`char *`." if scale == 1 else
                         f", not {sign * count:+d} byte(s)."))
            for item in re.finditer(rf"\b{name}\s*(\+\+|--)", code):
                sign = -1 if item.group(1) == "--" else 1
                facts.append(
                    f"C line {line_number}: `{item.group(0)}`; `{name}` is "
                    f"`{type_name} *`, so the raw address delta is "
                    f"{sign * size:+d} byte(s).")

    # Collapse the first simple store recurrence for each scalar pointer.  The
    # individual facts above are useful, but a model can still repair only one
    # term of an address whose initializer, update, and dereference are all
    # wrong together.
    for name, (type_name, size) in pointer_types.items():
        timeline: list[tuple[int, int, str, int]] = []
        for line_number, raw_line in enumerate(source.splitlines(), 1):
            code = re.sub(r"/\*.*?\*/", "", raw_line.split("//", 1)[0])
            patterns = (
                (rf"\b{name}\s*=\s*\([^()]*\*\)\s*\(\s*"
                 rf"\(\s*char\s*\*\s*\)\s*{name}\s*\+\s*"
                 rf"(?P<count>{_INTEGER_LITERAL})", "rawadd"),
                (rf"\b{name}\s*=\s*[A-Za-z_]\w*\s*\+\s*"
                 rf"(?P<count>{_INTEGER_LITERAL})", "set"),
                (rf"\b{name}\s*\+=\s*(?P<count>{_INTEGER_LITERAL})",
                 "add"),
                (rf"\b{name}\s*-=\s*(?P<count>{_INTEGER_LITERAL})",
                 "sub"),
                (rf"\*\s*{name}\s*=", "store"),
            )
            for pattern, operation in patterns:
                for item in re.finditer(pattern, code):
                    count = int(item.groupdict().get("count") or "0", 0)
                    if operation == "rawadd":
                        delta = count
                    elif operation == "set":
                        delta = count * size
                    elif operation == "add":
                        delta = count * size
                    elif operation == "sub":
                        delta = -count * size
                    else:
                        delta = 0
                    timeline.append(
                        (line_number, item.start(), operation, delta))
        timeline.sort()
        components: list[tuple[int, str, int]] = []
        for line_number, _column, operation, delta in timeline:
            if operation == "set":
                components = [(line_number, "initialization", delta)]
            elif operation in {"rawadd", "add", "sub"} and components:
                components.append((line_number, "update", delta))
            elif operation == "store" and components:
                components.append((line_number, "dereference", 0))
                facts.append(
                    f"C line {line_number}: store through `*{name}` uses raw "
                    f"address `{name} +0` bytes. For this `{type_name} *`, "
                    f"`{name}[k]` uses displacement `k*{size}` bytes, so "
                    f"`{name}[-1]` would use {-size:+d} bytes.")
                equation = " ".join(
                    f"{delta:+d} ({label} line {line_number})"
                    for line_number, label, delta in components)
                total = sum(delta for _line, _label, delta in components)
                facts.append(
                    f"First textual store recurrence through `{name}` "
                    f"(`{type_name} *`): base {equation} = base {total:+d} "
                    "raw byte(s). Check that this is the executed branch, then "
                    "repair every disagreeing term together.")
                break

    for layout in source_layout.layouts(source):
        claimed = [field for field in layout.fields if field.claims]
        if not claimed:
            continue
        facts.append(
            f"Compiler-recognized scalar layout prefix for `{layout.name}`: "
            f"alignment={layout.alignment}, computed size={layout.size} bytes. "
            "Comments do not affect either value.")
        for field in claimed:
            claims = ", ".join(
                f"{claim.source}=0x{claim.offset:x}"
                for claim in field.claims)
            verdict = "MATCH" if not field.mismatches else "MISMATCH"
            facts.append(
                f"  `{layout.name}.{field.name}` compiles at byte offset "
                f"0x{field.offset:x} (size {field.size}); comments/names claim "
                f"{claims}: {verdict}.")
    return list(dict.fromkeys(facts))[:limit]


def _observed_address_facts(feedback: str, limit: int = 8) -> list[str]:
    """Summarize target/candidate write offsets without slash notation."""
    location = (
        r"(?P<base>&?[A-Za-z_]\w*)"
        r"(?:\+0x(?P<offset>[0-9A-Fa-f]+))?"
        r"/(?P<width>[0-9]+)=(?P<value>0x[0-9A-Fa-f]+)")
    target_rows = list(re.finditer(
        rf"(?m)^\s*target:\s+{location}", feedback, re.I))
    candidate_rows = list(re.finditer(
        rf"(?m)^\s*candidate:\s+{location}", feedback, re.I))
    facts: list[str] = []
    for left, right in zip(target_rows, candidate_rows):
        left_base, left_offset, left_width = (
            left.group("base"), left.group("offset"), left.group("width"))
        right_base, right_offset, right_width = (
            right.group("base"), right.group("offset"), right.group("width"))
        if left_base != right_base:
            continue
        target_offset = int(left_offset or "0", 16)
        candidate_offset = int(right_offset or "0", 16)
        facts.append(
            f"Observed aligned write from `{left_base}`: target raw byte "
            f"offset {target_offset:+d}, candidate raw byte offset "
            f"{candidate_offset:+d}; candidate address error is "
            f"{candidate_offset - target_offset:+d} byte(s). Access widths are "
            f"target={left_width}, candidate={right_width} byte(s).")
    return list(dict.fromkeys(facts))[:limit]


def _opcode_facts(feedback: str, limit: int = 18) -> list[str]:
    """Decode opcodes already present in the causal windows into byte facts."""
    events = [(match.group("opcode"), (match.group("args") or "").strip())
              for match in _DYNAMIC_OPCODE.finditer(feedback)]
    present = {opcode for opcode, _args in events}
    store_bases: set[str] = set()
    for opcode, args in events:
        if opcode not in {"sb", "sh", "sw"}:
            continue
        address = re.search(
            r",\s*(?P<offset>-?(?:0x[0-9A-Fa-f]+|[0-9]+))"
            r"\((?P<base>[A-Za-z_]\w*)\)\s*$", args)
        if address and address.group("base") != "sp":
            store_bases.add(address.group("base"))
    facts = [
        "MIPS load/store offsets and `addiu` immediates are raw byte offsets; "
        "they are never scaled by a C pointed-to type."
    ]
    meanings = {
        "lb": "loads 1 byte and sign-extends it",
        "lbu": "loads 1 byte and zero-extends it",
        "lh": "loads 2 bytes and sign-extends them",
        "lhu": "loads 2 bytes and zero-extends them",
        "lw": "loads exactly 4 bytes",
        "sb": "stores exactly 1 byte",
        "sh": "stores exactly 2 bytes",
        "sw": "stores exactly 4 bytes",
    }
    for opcode, meaning in meanings.items():
        if opcode in present:
            facts.append(
                f"`{opcode}` {meaning}; its displacement is measured in bytes.")

    for opcode, args in events:
        if opcode not in {"sb", "sh", "sw"}:
            continue
        address = re.search(
            r",\s*(?P<offset>-?(?:0x[0-9A-Fa-f]+|[0-9]+))"
            r"\((?P<base>[A-Za-z_]\w*)\)\s*$", args)
        if not address or address.group("base") == "sp":
            continue
        offset = int(address.group("offset"), 0)
        width = {"sb": 1, "sh": 2, "sw": 4}[opcode]
        facts.append(
            f"Executed `{opcode} {args}` accesses exactly {width} byte(s) at "
            f"raw address `{address.group('base')} {offset:+d}` bytes.")

    for opcode, args in events:
        operands = [part.strip() for part in args.split(",")]
        if opcode == "addiu" and len(operands) == 3 and \
                (operands[0] in store_bases or operands[1] in store_bases):
            try:
                immediate = int(operands[2], 0)
            except ValueError:
                continue
            if abs(immediate) <= 0x20:
                facts.append(
                    f"Executed `{opcode} {args}` computes `{operands[0]} = "
                    f"{operands[1]} {immediate:+d}` raw byte(s); no C type "
                    "scaling is applied.")
        elif opcode in {"sll", "srl", "sra"} and len(operands) == 3:
            try:
                amount = int(operands[2], 0)
            except ValueError:
                continue
            if opcode == "sll":
                facts.append(
                    f"Executed `sll {args}` multiplies the low 32-bit value of "
                    f"`{operands[1]}` by {1 << amount}; it does not dereference "
                    "or apply C type scaling.")
    return list(dict.fromkeys(facts))[:limit]


def _equation_repair_facts(
        source: str, feedback: str, c_facts: list[str],
        limit: int = 4) -> list[str]:
    """Turn an exactly explained candidate address into bounded C hypotheses."""
    observed = _observed_address_facts(feedback)
    if not observed:
        return []
    offset_match = re.search(
        r"target raw byte offset (?P<target>[+-]\d+), candidate raw byte "
        r"offset (?P<candidate>[+-]\d+)", observed[0])
    if offset_match is None:
        return []
    target_offset = int(offset_match.group("target"))
    candidate_offset = int(offset_match.group("candidate"))
    if target_offset == candidate_offset:
        return []
    pointer_sizes: dict[str, tuple[str, int]] = {}
    for match in _C_POINTER_DECL.finditer(source):
        type_name = " ".join(re.sub(
            r"\b(?:const|volatile)\b", "", match.group("type")).split())
        size = _C_SCALAR_BYTES.get(type_name)
        if size is not None:
            pointer_sizes[match.group("name")] = (type_name, size)

    hypotheses: list[str] = []
    recurrence = re.compile(
        r"First textual store recurrence through `(?P<name>[A-Za-z_]\w*)`"
        r".*= base (?P<total>[+-]\d+) raw byte", re.S)
    for fact in c_facts:
        match = recurrence.search(fact)
        if match is None:
            continue
        name = match.group("name")
        total = int(match.group("total"))
        pointer = pointer_sizes.get(name)
        if pointer is None or total != candidate_offset:
            continue
        type_name, size = pointer
        required_displacement = target_offset - total
        if required_displacement % size != 0 or not re.search(
                rf"\*\s*{name}\s*=", source):
            continue
        index = required_displacement // size
        hypotheses.append(
            f"The CURRENT C recurrence for `{name}` exactly predicts the "
            f"observed candidate offset {candidate_offset:+d}. Holding its "
            f"verified initializer and updates fixed, the store needs a "
            f"{required_displacement:+d}-byte displacement to reach target "
            f"offset {target_offset:+d}. For `{type_name} *`, changing the "
            f"relevant `*{name}` store to `{name}[{index}]` supplies exactly "
            "that displacement. This is a bounded hypothesis; compile and "
            "differentially verify it on every case.")
        if len(hypotheses) >= limit:
            break
    return hypotheses


def mechanical_opcode_to_c_bridge(source: str, feedback: str) -> str:
    """Return compact negative evidence joining executed MIPS to C semantics."""
    c_facts = _c_address_and_layout_facts(source)
    opcode_facts = _opcode_facts(feedback)
    sections = [
        "TRACE NOTATION: `address/width=value` means the raw byte address, "
        "then access width in bytes. Thus `arg2+0x2/2` means byte offset +2 "
        "with a 2-byte access; the slash is not division or C pointer scaling.",
        "EXECUTED OPCODE FACTS:",
    ]
    sections.extend(f"- {fact}" for fact in _observed_address_facts(feedback))
    sections.extend(f"- {fact}" for fact in opcode_facts)
    sections.append("CURRENT C ADDRESS/LAYOUT FACTS:")
    if c_facts:
        sections.extend(f"- {fact}" for fact in c_facts)
    else:
        sections.append(
            "- No constant scalar-pointer arithmetic or claimed local layout "
            "was mechanically provable; use the raw executed evidence.")
    repair_facts = _equation_repair_facts(source, feedback, c_facts)
    if repair_facts:
        sections.append("CONTROLLER-DERIVED ADDRESS REPAIR HYPOTHESES:")
        sections.extend(f"- {fact}" for fact in repair_facts)
    return "\n".join(sections)


_MIPS_REGISTER = re.compile(
    r"\b(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra)\b")


def _diff_instruction_sides(
        diff: str) -> tuple[list[tuple[str, str, str]],
                            list[tuple[str, str, str]]]:
    removed: list[tuple[str, str, str]] = []
    added: list[tuple[str, str, str]] = []
    for raw in diff.splitlines():
        match = re.match(r"^([-+])(?![-+])\s*([.$\w]+)\s*(.*)$", raw)
        if not match:
            continue
        row = (match.group(2).lower(), match.group(3).strip(), raw[1:].strip())
        (removed if match.group(1) == "-" else added).append(row)
    return removed, added


def _register_operands_only(
        removed: list[tuple[str, str, str]],
        added: list[tuple[str, str, str]]) -> bool:
    """Whether paired residual instructions differ only in register operands.

    Unlike ``_register_renaming_only``, this permits one physical register to
    represent different C live ranges at different points.  That is the common
    shape of a register-allocation cycle and cannot be represented by one
    global register bijection.
    """
    if len(removed) != len(added) or not removed:
        return False
    for target, candidate in zip(removed, added):
        target_text = f"{target[0]} {target[1]}".strip()
        candidate_text = f"{candidate[0]} {candidate[1]}".strip()
        if _MIPS_REGISTER.sub("REG", target_text) != \
                _MIPS_REGISTER.sub("REG", candidate_text):
            return False
    return any(target != candidate
               for target, candidate in zip(removed, added))


def _register_or_local_order_only(
        removed: list[tuple[str, str, str]],
        added: list[tuple[str, str, str]]) -> bool:
    """Whether register-erased instructions differ only by local ordering."""
    if len(removed) != len(added) or not removed:
        return False
    def normalize(row: tuple[str, str, str]) -> str:
        return _MIPS_REGISTER.sub(
            "REG", f"{row[0]} {row[1]}".strip())
    target = sorted(normalize(row) for row in removed)
    candidate = sorted(normalize(row) for row in added)
    # When one identical instruction moved, a unified diff contains the same
    # deleted and inserted row at different hunk positions.  The row lists are
    # then equal even though the full streams are not; the presence of changed
    # rows already proves that this is not an unchanged object.
    return target == candidate


def register_operands_only(diff: str) -> bool:
    return _register_operands_only(*_diff_instruction_sides(diff))


def register_or_local_order_only(diff: str) -> bool:
    return _register_or_local_order_only(*_diff_instruction_sides(diff))


def exactness_residual_classification(diff: str) -> str:
    removed, added = _diff_instruction_sides(diff)
    if _register_operands_only(removed, added):
        return (
            "REGISTER-OPERAND-ONLY: paired changed instructions have the same "
            "opcodes, order, immediates, memory displacements, and symbols "
            "after physical register names are erased. Treat this as a C "
            "live-range/allocation/source-order problem. There is no residual "
            "evidence for changing a value, pointer offset, or condition.")
    if _register_or_local_order_only(removed, added):
        return (
            "REGISTER/LOCAL-ORDER-ONLY: target and candidate have the same "
            "changed opcode/immediate/displacement/symbol multiset after "
            "physical registers are erased. A small instruction ordering and "
            "allocation cycle remains. Treat it as a C independent-statement, "
            "live-range, or scheduling problem. There is no residual evidence "
            "for changing a value, pointer offset, or condition.")
    return (
        "MIXED STATIC RESIDUAL: at least one paired changed instruction still "
        "differs after physical registers are erased. Explain that exact "
        "opcode/immediate/displacement difference, while preserving every "
        "target-observable behavior already verified.")


def _register_renaming_only(
        removed: list[tuple[str, str, str]],
        added: list[tuple[str, str, str]]) -> bool:
    """Whether a residual is one instruction stream under a register bijection.

    This is stronger than comparing opcode counts: constants, memory offsets,
    symbols, and instruction order must all already match.  The result is the
    signature of an IDO allocation/source-order problem, not missing logic.
    """
    if len(removed) != len(added) or len(removed) < 2:
        return False
    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}
    for target, candidate in zip(removed, added):
        target_text = f"{target[0]} {target[1]}".strip()
        candidate_text = f"{candidate[0]} {candidate[1]}".strip()
        if _MIPS_REGISTER.sub("REG", target_text) != \
                _MIPS_REGISTER.sub("REG", candidate_text):
            return False
        target_registers = _MIPS_REGISTER.findall(target_text)
        candidate_registers = _MIPS_REGISTER.findall(candidate_text)
        if len(target_registers) != len(candidate_registers):
            return False
        for left, right in zip(target_registers, candidate_registers):
            if forward.setdefault(left, right) != right:
                return False
            if reverse.setdefault(right, left) != left:
                return False
    return any(left != right for left, right in forward.items())


def register_renaming_only(diff: str) -> bool:
    return _register_renaming_only(*_diff_instruction_sides(diff))


def deterministic_exactness_candidates(
        source: str, function: str, diff: str,
        max_variants: int = 48,
        policy: transition_policy.TransitionPolicy | None = None,
        source_history=(), direct_attribution=None
        ) -> tuple[principle_variants.Variant, ...]:
    """A zero-token search over code shapes and allocation source levers.

    Candidate generation stays exhaustive within its bounded budget.  A
    cross-function transition policy may change only the order of those
    experiments; compile, semantic replay, and exact verification still gate
    every result.
    """
    from solver import (code_shapes, residual_alternatives, rng_alternatives,
                        timer_alternatives, callback_alternatives, callback_reload_alternatives,
                        callback_structural_hypotheses)

    if max_variants <= 0:
        return ()
    mapping = residual_sites.source_map(source, function, diff, direct_attribution)
    shape_variants = list(code_shapes.candidates(
        source, function, max_variants=max_variants, allow_do_while=False,
        priority=lambda variant: residual_sites.rank(source, variant, mapping, source_history)))
    parameter_reuse = list(
        principle_variants.reuse_dead_parameter_for_local(source, function))
    variants: list[principle_variants.Variant] = []
    seen = {_sha(source)}
    family_budget = max(1, (max_variants + 3) // 4)
    residual_variants = parameter_reuse + [
        principle_variants.Variant(rewrite.label, rewrite(source))
        for rewrite in rewrites.statement_order_rewrites(
            source, diff, gate=False, max_variants=family_budget)
    ]
    lifetime_variants = list(principle_variants.isolated_register_web(
        source, function, max_variants=family_budget))
    epoch_variants = [
        principle_variants.Variant(row.label, row.source)
        for row in exactness_gradient.split_epoch_experiments(
            source, limit=family_budget)
    ]
    # Round-robin prevents a prolific statement-order generator from silently
    # consuming the complete budget before declaration/lifetime probes fire.
    repairs = list(residual_alternatives.representation(source, function, diff, direct_attribution))
    repairs += list(residual_alternatives.masked_parameter_storage(source, function, diff))
    region = code_shapes._body(source, function)
    if region:
        for rewrite in rewrites.propose(source, diff):
            changed = rewrite(source)
            edit = residual_sites.edit_region(source, changed)
            if region[1] <= edit['start'] and edit['stop'] <= region[2]:
                if not re.search(r'\bdo\b', code_shapes._mask(changed[region[1]:])):
                    repairs.append(principle_variants.Variant(rewrite.label, changed))
    expressions = list(residual_alternatives.arithmetic_staging(source, function)) + list(residual_alternatives.expression_lifetimes(source, function))
    idioms = (list(rng_alternatives.candidates(source, function, maximum=family_budget)) +
              list(timer_alternatives.candidates(source, function, maximum=family_budget)) +
              list(callback_alternatives.masked_parameter_casts(source, function, maximum=family_budget)) +
              list(callback_reload_alternatives.candidates(source, function, maximum=1)) +
              list(callback_structural_hypotheses.candidates(source, function, maximum=1)))
    families = (idioms, repairs, expressions, shape_variants, epoch_variants, residual_variants, lifetime_variants)
    for index in range(max((len(family) for family in families), default=0)):
        for family in families:
            if index >= len(family):
                continue
            variant = family[index]
            digest = _sha(variant.source)
            if digest in seen:
                continue
            seen.add(digest)
            variants.append(variant)
    variants.sort(key=lambda variant: residual_sites.rank(source, variant, mapping, source_history))
    pairs = residual_alternatives.independent_pairs(source, variants, maximum=min(8, max_variants // 4))
    # Reserve a small explicit combination budget: neutral single edits need
    # not first survive a frontier expansion in order to be tested together.
    variants = variants[:max_variants - len(pairs)] + list(pairs)
    variants = variants[:max_variants]
    if policy is not None:
        state = transition_policy.residual_state(diff, source)
        variants.sort(
            key=lambda variant: policy.estimate(
                variant.label, state,
                relation="deterministic-exactness-search",
                exclude_function=function).rank_key(),
            reverse=True)
    return tuple(variants)


def deterministic_semantic_candidates(
        source: str, feedback: str, function: str = ""
        ) -> tuple[principle_variants.Variant, ...]:
    """Materialize mechanically proven address-expression alternatives.

    These are experiments, never automatic claims.  They are emitted only
    when the executed target/candidate address equation plus the current C
    element size determines an integral index, or when a pre-decrement is
    imitating a non-mutating target store displacement.  The ordinary compile
    and full differential gates remain authoritative.
    """
    bridge = mechanical_opcode_to_c_bridge(source, feedback)
    candidates: list[principle_variants.Variant] = []
    seen: set[str] = set()
    lowered_feedback = feedback.lower()
    if (function and "target" in lowered_feedback and
            "candidate" in lowered_feedback and "differs" in lowered_feedback):
        candidates.extend(
            principle_variants.materialize_aliased_rhs_before_write(
                source, function))
    equation = re.compile(
        r"changing the relevant `\*(?P<name>[A-Za-z_]\w*)` store to "
        r"`(?P=name)\[(?P<index>-?[0-9]+)\]`")
    for match in equation.finditer(bridge):
        name, index = match.group("name"), match.group("index")
        store = re.search(rf"\*\s*{name}\s*=", source)
        if store is None:
            continue
        edited = source[:store.start()] + f"{name}[{index}] =" + \
            source[store.end():]
        if edited not in seen:
            seen.add(edited)
            candidates.append(principle_variants.Variant(
                f"equation-derived-store-index-{name}-{index}", edited))

    for match in re.finditer(
            r"\*\s*--\s*(?P<name>[A-Za-z_]\w*)\s*=", source):
        name = match.group("name")
        if f"`{name}[-1]` supplies the same" not in bridge:
            continue
        edited = source[:match.start()] + f"{name}[-1] =" + \
            source[match.end():]
        if edited not in seen:
            seen.add(edited)
            candidates.append(principle_variants.Variant(
                f"nonmutating-store-displacement-{name}--1", edited))
    return tuple(candidates)


def exactness_principles(diff: str, source: str = "") -> str:
    """Translate narrow opcode residuals into reusable C-shape hypotheses."""
    removed, added = _diff_instruction_sides(diff)

    hints: list[str] = []
    if _register_renaming_only(removed, added):
        hints.append(
            "- The changed block already has identical opcodes, constants, "
            "memory offsets, symbols, and instruction order under a consistent "
            "register rename. This is a register-allocation-only residual. "
            "Before changing expressions, enumerate the order of adjacent "
            "independent C statements in this basic block; IDO can retain the "
            "scheduled instruction order while changing its temporary-register "
            "assignment.")
    elif _register_operands_only(removed, added):
        hints.append(
            "- Every paired changed instruction has the same opcode, constant, "
            "memory displacement, symbol, and position after physical register "
            "names are erased. The mapping changes across non-overlapping live "
            "ranges, so one global register rename is insufficient. Treat this "
            "as an allocation/lifetime cycle: vary declaration/initializer "
            "placement, scope, independent statement order, or temporary "
            "materialization without changing values or addresses.")
    elif _register_or_local_order_only(removed, added):
        hints.append(
            "- The register-erased changed instructions have an identical "
            "opcode/immediate/displacement/symbol multiset but a small local "
            "ordering difference. Search independent C statement order and "
            "live-range construction. Do not change literals, pointer offsets, "
            "conditions, or data values to solve a scheduling residual.")
    for target_op, target_args, _text in removed:
        for candidate_op, candidate_args, _candidate_text in added:
            if target_args != candidate_args:
                continue
            operand = target_args or "the load site"
            if target_op == "lh" and candidate_op == "lhu":
                hints.append(
                    f"- `{operand}`: target signed `lh` versus candidate `lhu` "
                    "usually requires a signed 16-bit C declaration/expression.")
            elif target_op == "lb" and candidate_op == "lbu":
                hints.append(
                    f"- `{operand}`: target signed `lb` versus candidate `lbu` "
                    "usually requires a signed 8-bit C declaration/expression.")
            elif target_op == "lhu" and candidate_op == "lh":
                hints.append(
                    f"- `{operand}`: target `lhu` versus candidate signed `lh` "
                    "usually requires an unsigned 16-bit C declaration/expression.")
            elif target_op == "lbu" and candidate_op == "lb":
                hints.append(
                    f"- `{operand}`: target `lbu` versus candidate signed `lb` "
                    "usually requires an unsigned 8-bit C declaration/expression.")

    removed_text = {text for _op, _args, text in removed}
    added_text = {text for _op, _args, text in added}
    for text in sorted(removed_text & added_text):
        if text.split(None, 1)[0].lower() in {"lb", "lbu", "lh", "lhu", "lw"}:
            hints.append(
                f"- `{text}` appears on both sides at a different position: "
                "this is a scheduling/evaluation-order residual. Prefer a local "
                "temporary loaded at the target position and reused later; do not "
                "duplicate arithmetic merely to move the load.")
            offset = re.search(r",\s*(0x[0-9a-fA-F]+)\s*\(", text)
            if offset and source:
                value = int(offset.group(1), 16)
                macros = []
                for line in source.splitlines():
                    macro = re.match(
                        r"\s*#define\s+([A-Za-z_]\w*)\([^)]*\).*\+\s*"
                        r"(0x[0-9a-fA-F]+)", line)
                    if macro and int(macro.group(2), 16) == value:
                        macros.append(macro.group(1))
                for macro in macros[:3]:
                    uses = [line.strip() for line in source.splitlines()
                            if f"{macro}(" in line and not
                            line.lstrip().startswith("#define")]
                    hints.append(
                        f"- Source correlation: offset `{offset.group(1)}` is "
                        f"accessed through `{macro}(...)`. Materialize that value "
                        "in a same-width local assigned where the target load "
                        "appears, then reuse the local in its later value uses. "
                        "Relevant current lines: " + " | ".join(uses[:4]))
    layout_warning = source_layout.prompt_warning(source) if source else ""
    if layout_warning:
        hints.append(layout_warning)
    return "\n".join(dict.fromkeys(hints)) if hints else (
        "- No narrow signedness or moved-load principle was recognized; reason "
        "from the exact residual without changing verified semantics.")


def build_diagnosis_prompt(
        target_assembly: str, candidate_assembly: str, source: str,
        attempt: workspace.Attempt,
        results: list[differential.DifferentialResult],
        rejected: list[str], divergence_bundle: str = "",
        project_context: str = "",
        exactness_history: tuple[dict, ...] | list[dict] = ()) -> str:
    semantics_pass = all(row.status == "passed" for row in results)
    observed_clean = _observed_semantics_clean(results)
    if observed_clean and attempt.diff:
        target_assembly = (
            "(full static body omitted in exactness phase; the complete "
            "unresolved target instructions are the `-` lines in CURRENT "
            "EXACT ASSEMBLY RESIDUAL)")
        candidate_assembly = (
            "(full static body omitted in exactness phase; the complete "
            "unresolved candidate instructions are the `+` lines in CURRENT "
            "EXACT ASSEMBLY RESIDUAL)")
    elif any(row.status == "failed" for row in results):
        target_assembly = (
            "(full static body omitted while a semantic failure remains; "
            "use the executed target windows in DYNAMIC CAUSAL EVIDENCE)")
        candidate_assembly = (
            "(full static body omitted while a semantic failure remains; "
            "use the executed candidate windows in DYNAMIC CAUSAL EVIDENCE)")
    feedback = _prioritized_feedback(results, source, divergence_bundle)
    if observed_clean and attempt.diff:
        gradient = exactness_gradient.build(
            attempt.diff, source, history=exactness_history,
            rejected=rejected)
        register_shape_only = gradient.residual_classification.startswith(
            ("register-operand-only", "register-or-local-order-only"))
        static_residual_context = (
            "(raw residual withheld: the deterministic feed above supplies "
            "the full-stream classification, correspondence cycles, anchor "
            "opcode pairs, entry ABI map, and experiment history. This "
            "prevents physical register spelling from being mistaken for C "
            "semantics.)"
            if register_shape_only else
            "```\n" + attempt.diff[:12000] + "\n```"
        )
        return EXACTNESS_DIAGNOSIS_PROMPT.format(
            phase=(
                "BYTE EXACTNESS: every finite semantic case passes."
                if semantics_pass else
                "BYTE EXACTNESS WITH INCONCLUSIVE TARGET COVERAGE: every "
                "target-observable case passes; target-side execution-limit "
                "cases remain explicit coverage debt."),
            residual_classification=exactness_residual_classification(
                attempt.diff),
            exactness_feed=gradient.render(),
            static_residual_context=static_residual_context,
            source=source,
            principles=exactness_principles(attempt.diff, source),
            feedback=feedback,
            project_context=(project_context or
                             "(no project header context supplied)"),
            compiled=attempt.compiled,
            score=attempt.score,
            exact=attempt.exact,
            rejected=("\n".join(f"- {item}" for item in rejected[-6:])
                      if rejected else "(none)"))
    return DIAGNOSIS_PROMPT.format(
        target_assembly=target_assembly,
        candidate_assembly=candidate_assembly, source=source,
        project_context=(project_context or
                         "(no project header context supplied)"),
        feedback=feedback,
        mechanical_bridge=mechanical_opcode_to_c_bridge(source, feedback),
        compiled=attempt.compiled,
        score=attempt.score, exact=attempt.exact,
        phase=(
            "BYTE EXACTNESS: every finite semantic case passes. Preserve every "
            "verified behavior and explain the exact compiler residual."
            if semantics_pass else
            ("BYTE EXACTNESS WITH INCONCLUSIVE TARGET COVERAGE: every "
             "target-observable case passes. Target-side execution-limit "
             "cases are coverage debt, not candidate failures. Preserve the "
             "passing behavior and explain only the static compiler residual; "
             "do not invent a semantic pointer/value error from different "
             "physical register names."
             if observed_clean else
             "LOGIC FIRST: repair the next dynamic semantic divergence before "
             "optimizing instruction shape.")),
        diff=(attempt.diff or "(no compiler diff recorded)")[:12000],
        principles=exactness_principles(attempt.diff or "", source),
        rejected=("\n".join(f"- {item}" for item in rejected[-6:])
                  if rejected else "(none)"))


def _number_source(source: str) -> str:
    """Render stable one-based source locations for the patch-only prompt."""
    lines = source.splitlines()
    if not lines:
        return "   1 |"
    width = max(4, len(str(len(lines))))
    return "\n".join(
        f"{index:>{width}} | {line}"
        for index, line in enumerate(lines, start=1))


_SIMPLE_STATEMENT_HEAD = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)\s*"
    r"(?P<operator>\+\+|--|\+=|-=|\*=|/=|=)")


def _preserve_unrelated_sibling_statements(
        old_line: str, replacement: str) -> str:
    """Splice one uniquely targeted statement without deleting its siblings."""
    if "\n" in old_line.rstrip("\r\n") or "\n" in replacement.rstrip("\n"):
        return replacement
    replacement_code = replacement.strip()
    if replacement_code.count(";") != 1 or \
            any(token in replacement_code for token in "{}"):
        return replacement
    replacement_head = _SIMPLE_STATEMENT_HEAD.match(
        replacement_code.rstrip(";"))
    if replacement_head is None:
        return replacement
    name = replacement_head.group("name")
    pieces = old_line.rstrip("\r\n").split(";")
    statements = [piece for piece in pieces[:-1] if piece.strip()]
    if len(statements) <= 1:
        return replacement
    matching = [index for index, statement in enumerate(statements)
                if (head := _SIMPLE_STATEMENT_HEAD.match(statement)) and
                head.group("name") == name]
    if len(matching) != 1:
        return replacement
    index = matching[0]
    leading = re.match(r"^\s*", statements[index]).group(0)
    statements[index] = leading + replacement_code.rstrip(";")
    ending = "\n" if old_line.endswith("\n") else ""
    return ";".join(statements) + ";" + ending


def _preserve_span_sibling_statements(old: str, replacement: str) -> str:
    """Apply the unique-statement splice when a model selected extra lines."""
    if "\n" not in old.rstrip("\r\n"):
        return _preserve_unrelated_sibling_statements(old, replacement)
    old_lines = old.splitlines(keepends=True)
    new_lines = replacement.splitlines(keepends=True)
    for new_index, new_line in enumerate(new_lines):
        head = _SIMPLE_STATEMENT_HEAD.match(new_line.strip().rstrip(";"))
        if head is None:
            continue
        name = head.group("name")
        candidates = []
        for old_line in old_lines:
            statements = [piece for piece in old_line.rstrip(
                "\r\n").split(";")[:-1] if piece.strip()]
            if len(statements) <= 1:
                continue
            matches = [statement for statement in statements
                       if (old_head := _SIMPLE_STATEMENT_HEAD.match(
                           statement)) and old_head.group("name") == name]
            if len(matches) == 1:
                candidates.append(old_line)
        if len(candidates) != 1:
            continue
        new_lines[new_index] = _preserve_unrelated_sibling_statements(
            candidates[0], new_line)
    return "".join(new_lines)


def parse_and_apply_patch(
        text: str, source: str) -> tuple[modelrepair.Proposal, str, str]:
    """Resolve a model-selected line span without trusting copied source text.

    Older exact-text proposals remain readable for receipt replay, but the v11
    prompts request bounded source spans. The controller resolves them against
    the exact source it displayed and enforces the same escape, growth, and
    semantic-change bounds as the general repair engine.
    """
    value = next(modelrepair._objects(text), None)
    if value is None:
        raise ValueError("no JSON object")
    if "spans" not in value and "content" not in value:
        proposal = modelrepair.parse_proposal(
            text, truncate_hypothesis=True, normalize_kind=True,
            default_hypothesis=True)
        return (proposal, modelrepair.apply_proposal(
            source, proposal, relaxed_whitespace=True), "exact-text")

    kind = value.get("kind")
    if kind not in modelrepair.KINDS:
        kind = "other"
    hypothesis = value.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        hypothesis = "model-proposed bounded source-span repair"
    hypothesis = hypothesis.strip()
    if len(hypothesis) > 400:
        hypothesis = hypothesis[:397].rstrip() + "..."

    spans = value.get("spans", value.get("content"))
    if (not isinstance(spans, list) or
            not 1 <= len(spans) <= MAX_SOURCE_SPANS):
        raise ValueError(
            f"spans must contain 1 to {MAX_SOURCE_SPANS} source intervals")
    lines = source.splitlines(keepends=True)
    if not lines:
        raise ValueError("cannot edit an empty source")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))

    normalized = []
    for raw in spans:
        if isinstance(raw, (list, tuple)) and len(raw) == 3:
            start, end, replacement = raw
        elif isinstance(raw, dict):
            start = raw.get("start_line", raw.get("start"))
            end = raw.get("end_line", raw.get("end"))
            replacement = raw.get("new")
        else:
            raise ValueError("source span must be an object or 3-item array")
        if isinstance(start, str) and start.strip().isdigit():
            start = int(start.strip())
        if isinstance(end, str) and end.strip().isdigit():
            end = int(end.strip())
        if (isinstance(start, bool) or isinstance(end, bool) or
                not isinstance(start, int) or not isinstance(end, int)):
            raise ValueError("source span line numbers must be integers")
        if isinstance(replacement, list) and all(
                isinstance(item, str) for item in replacement):
            replacement = "\n".join(replacement)
        if not isinstance(replacement, str):
            raise ValueError("source span replacement must be text")
        replacement = "\n".join(
            (match.group(1) if (match := re.match(
                r"^\s*\d+\s*\| ?(.*)$", line)) else line)
            for line in replacement.split("\n"))
        if start < 1 or end < start or end > len(lines):
            raise ValueError(
                f"source span {start}-{end} is outside lines 1-{len(lines)}")
        old = "".join(lines[start - 1:end])
        if old.endswith("\r\n") and not replacement.endswith(("\r", "\n")):
            replacement += "\r\n"
        elif old.endswith("\n") and not replacement.endswith(("\r", "\n")):
            replacement += "\n"
        replacement = _preserve_span_sibling_statements(old, replacement)
        if old == replacement:
            continue
        if modelrepair.FORBIDDEN_EDIT.search(replacement):
            raise ValueError("edit introduces a forbidden source escape")
        comment_tokens = lambda value: len(re.findall(r"//|/\*", value))
        if comment_tokens(replacement) > comment_tokens(old):
            raise ValueError("edit adds explanatory comments instead of code")
        normalized.append((start, end, old, replacement))

    if not normalized:
        raise ValueError("source span edit is a no-op")
    normalized.sort(key=lambda item: item[0])
    if any(left[1] >= right[0]
           for left, right in zip(normalized, normalized[1:])):
        raise ValueError("source spans overlap")
    if sum(end - start + 1 for start, end, _old, _new in normalized) > \
            MAX_SOURCE_SPAN_LINES:
        raise ValueError(
            f"source spans exceed {MAX_SOURCE_SPAN_LINES} changed lines")
    if (normalized[0][0] == 1 and normalized[-1][1] == len(lines) and
            sum(end - start + 1 for start, end, _old, _new in normalized)
            == len(lines)):
        raise ValueError("whole-file replacement is not allowed")
    if sum(len(old) + len(new) for _start, _end, old, new in normalized) > \
            modelrepair.MAX_EDIT_CHARS:
        raise ValueError("edit budget exceeded")

    out = source
    for start, end, _old, replacement in reversed(normalized):
        out = out[:offsets[start - 1]] + replacement + out[offsets[end]:]
    if len(out) - len(source) > modelrepair.MAX_GROWTH:
        raise ValueError("proposal grows the source too much")
    if modelrepair._semantic_text(out) == modelrepair._semantic_text(source):
        raise ValueError("edit changes comments or whitespace only")
    proposal = modelrepair.Proposal(kind, hypothesis, tuple(
        modelrepair.Edit(old, replacement)
        for _start, _end, old, replacement in normalized))
    return proposal, out, ("source-span" if len(normalized) == 1
                           else "source-spans")


_POINTER_STORE_ASSIGNMENT = re.compile(
    r"(?P<lhs>\*\s*[A-Za-z_]\w*|[A-Za-z_]\w*\s*\[[^\]]+\])\s*=(?!=)")


def _pointer_store_lvalues(source: str) -> tuple[str, ...]:
    return tuple(re.sub(r"\s+", "", match.group("lhs"))
                 for match in _POINTER_STORE_ASSIGNMENT.finditer(source))


def _pointer_state_signatures(source: str,
                              names: set[str]) -> tuple[str, ...]:
    statements = re.findall(r"[^;{}]+;", source)
    signatures = []
    for statement in statements:
        code = re.sub(r"/\*.*?\*/|//[^\r\n]*", "", statement,
                      flags=re.S)
        if any(re.search(
                rf"(?:\b{re.escape(name)}\s*(?:=|\+=|-=|\+\+|--)|"
                rf"(?:\+\+|--)\s*{re.escape(name)}\b)", code)
               for name in names):
            signatures.append(re.sub(r"\s+", "", code))
    return tuple(signatures)


def validate_observable_locks(
        source: str, edited: str,
        results: list[differential.DifferentialResult]) -> None:
    """Reject edits aimed solely at operands the debugger proved exact."""
    selected = None
    for row in results:
        index = _write_prefix(row)
        if index < len(row.target.writes) and \
                index < len(row.candidate.writes):
            selected = (row.target.writes[index], row.candidate.writes[index])
            break
    if selected is None:
        return
    target, candidate = selected
    if target.address == candidate.address and target.width != candidate.width:
        pointer = differential._pointer_parts(candidate.address)
        if pointer:
            old_fields = [field for layout in source_layout.layouts(source)
                          for field in layout.fields
                          if field.offset == pointer[1] and field.size == candidate.width]
            new_fields = {(field.struct, field.name): field
                          for layout in source_layout.layouts(edited) for field in layout.fields}
            if len(old_fields) == 1:
                old = old_fields[0]
                new = new_fields.get((old.struct, old.name))
                if new and new.size == target.width and new.offset != old.offset:
                    raise ValueError(
                        f"width-only repair moved {old.struct}.{old.name} from "
                        f"+0x{old.offset:x} to +0x{new.offset:x}. The debugger "
                        f"proved its byte address exact: preserve preceding padding "
                        f"and offset +0x{old.offset:x}; change only access width "
                        f"{candidate.width}->{target.width}.")
    if target.address != candidate.address or \
            target.width != candidate.width or \
            target.value == candidate.value:
        return

    old_lvalues = _pointer_store_lvalues(source)
    new_lvalues = _pointer_store_lvalues(edited)
    pointer_names = {
        match.group(1)
        for lvalue in old_lvalues
        if (match := re.search(r"([A-Za-z_]\w*)", lvalue))
    }
    pointer_state_changed = _pointer_state_signatures(
        source, pointer_names) != _pointer_state_signatures(
            edited, pointer_names)
    old_types = {
        match.group("name"): " ".join(match.group("type").split())
        for match in _C_POINTER_DECL.finditer(source)
        if match.group("name") in pointer_names
    }
    new_types = {
        match.group("name"): " ".join(match.group("type").split())
        for match in _C_POINTER_DECL.finditer(edited)
        if match.group("name") in pointer_names
    }
    if old_lvalues != new_lvalues or pointer_state_changed or \
            old_types != new_types:
        raise ValueError(
            "observable lock violation: the next store ADDRESS and WIDTH are "
            "already EXACT while VALUE differs; preserve pointer-store "
            "lvalues, pointer updates, and pointee types, and edit only the "
            "wrong value producer or the predicate that selected it")


_C_COMMENT_OR_LITERAL = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*',
    re.DOTALL)
_C_VALUE_LITERAL = re.compile(
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
    r'(?<![A-Za-z0-9_])(?:0[xX][0-9a-fA-F]+|'
    r'(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|'
    r'\d+(?:[eE][+-]?\d+)?)(?:[uUlLfF]+)?(?![A-Za-z0-9_])')
_C_CONTROL_HEAD = re.compile(r"\b(?:if|for|while|switch)\s*\(")


def _comments_blanked(source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if not text.startswith("/"):
            return text
        return re.sub(r"[^\n]", " ", text)
    return _C_COMMENT_OR_LITERAL.sub(replace, source)


def _source_value_literals(source: str) -> tuple[str, ...]:
    """Stable literal bag, excluding comments and digits inside identifiers."""
    return tuple(sorted(
        match.group(0).lower()
        for match in _C_VALUE_LITERAL.finditer(_comments_blanked(source))))


def _control_conditions(source: str) -> tuple[str, ...]:
    """Extract ordered control predicates with balanced parentheses."""
    masked = c89._mask(source)
    conditions: list[str] = []
    for match in _C_CONTROL_HEAD.finditer(masked):
        start = masked.find("(", match.start())
        depth = 0
        end = start
        while end < len(masked):
            char = masked[end]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        if depth == 0:
            conditions.append(re.sub(r"\s+", "", masked[start:end]))
    return tuple(conditions)


def _declared_local_names(source: str) -> set[str]:
    """Simple C89 local declarations, sufficient for shape-edit validation."""
    return {
        match.group("name")
        for line in c89._mask(source).splitlines()
        if (match := c89.DECL_RE.match(line)) is not None and
        not match.group("type").strip().startswith(("extern ", "static "))
    }


def _declared_local_counts(source: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in c89._mask(source).splitlines():
        match = c89.DECL_RE.match(line)
        if match is None:
            continue
        name = match.group("name")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _identifier_occurrences(source: str, name: str) -> int:
    return len(re.findall(rf"\b{re.escape(name)}\b", c89._mask(source)))


def _local_value_uses(source: str, name: str) -> int:
    """Occurrences of a local beyond its declaration and simple definitions."""
    masked = c89._mask(source)
    declarations = sum(
        1 for line in masked.splitlines()
        if (match := c89.DECL_RE.match(line)) is not None and
        match.group("name") == name)
    definitions = len(re.findall(
        rf"(?<![\w.>*])\b{re.escape(name)}\b\s*=\s*(?!=)", masked))
    return max(0, _identifier_occurrences(source, name) -
               declarations - definitions)


def validate_exactness_shape_edit(
        source: str, edited: str,
        results: list[differential.DifferentialResult], diff: str) -> None:
    """Reject unsupported semantic edits to a register-operand-only residual.

    Dynamic cases are finite and some target paths may be inconclusive.  Once
    all target-observable cases pass and the static residual changes only
    physical registers, score improvement is evidence for source *shape*, not
    authority to alter unobserved values.  This guard blocks the two most
    common hallucinations while leaving lifetime/order/temp experiments open.
    """
    if not _observed_semantics_clean(results) or \
            not register_or_local_order_only(diff):
        return
    if _source_value_literals(source) != _source_value_literals(edited):
        raise ValueError(
            "exactness-shape violation: the residual contains only physical-"
            "register and local-order differences and every target-observable "
            "case passes, so it does not support changing numeric/string/"
            "character literals. "
            "Preserve all constants and propose only a declaration, lifetime, "
            "scope, independent-order, temporary, or equivalent-expression "
            "shape experiment")
    if _control_conditions(source) != _control_conditions(edited):
        raise ValueError(
            "exactness-shape violation: the residual contains only physical-"
            "register and local-order differences and supplies no evidence for "
            "changing an if, for, while, or switch condition. Preserve control "
            "predicates and "
            "propose only a semantics-preserving source-shape experiment")
    old_locals = _declared_local_names(source)
    new_locals = _declared_local_names(edited)
    old_counts = _declared_local_counts(source)
    new_counts = _declared_local_counts(edited)
    duplicate = sorted(
        name for name, count in new_counts.items()
        if count > 1 and count > old_counts.get(name, 0))
    if duplicate:
        raise ValueError(
            "incomplete value-epoch split: local "
            f"`{duplicate[0]}` is declared multiple times after C89 "
            "normalization. Keep its original declaration once and add a "
            "distinctly named same-typed local for the selected epoch")
    dangling = sorted(
        name for name in old_locals - new_locals
        if _identifier_occurrences(edited, name))
    if dangling:
        name = dangling[0]
        raise ValueError(
            "incomplete value-epoch split: the declaration for "
            f"`{name}` was removed or renamed but "
            f"{_identifier_occurrences(edited, name)} definition/use tokens "
            "remain. Keep the original declaration and add a second local, "
            "or rename every selected epoch definition and required use; "
            "an epoch split requires at least two source spans")
    unused = sorted(
        name for name in new_locals - old_locals
        if _local_value_uses(edited, name) == 0)
    if unused:
        raise ValueError(
            "incomplete value-epoch split: newly declared local "
            f"`{unused[0]}` has no value use beyond its declaration and "
            "definitions. Keep the original executable "
            "assignment, add the new declaration beside existing locals, "
            "and rename the selected epoch's definition and required uses; "
            "an epoch split requires at least two source spans")


def _compact_diagnosis(text: str, max_chars: int = 8000) -> str:
    """Pass only a completed bounded diagnosis to the patch-emission phase."""
    # Model headings may use Markdown and typographic hyphens. Normalize only
    # heading lines, never quoted C or the substance of the proposed change.
    lines = []
    for line in text.splitlines():
        heading = line.strip().lstrip("#*").strip()
        if re.match(r"^[1-5]\.\s+[A-Z]", heading):
            line = heading.replace("**", "").translate(str.maketrans(
                {"\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-"}))
        lines.append(line)
    text = "\n".join(lines)
    label_sets = (
        (
            "1. NEXT BAD OBSERVABLE",
            "2. TARGET VALUE/ADDRESS PROVENANCE",
            "3. CANDIDATE VALUE/ADDRESS PROVENANCE",
            "4. SOURCE-LEVEL CAUSE",
            "5. MINIMAL PATCH PLAN",
        ),
        (
            "1. RESIDUAL CLASSIFICATION",
            "2. TARGET REGISTER/INSTRUCTION ROLES",
            "3. CANDIDATE C LIVE RANGES AND SOURCE ORDER",
            "4. SEMANTICS-PRESERVING SOURCE-SHAPE CAUSE",
            "5. MINIMAL PATCH PLAN",
        ),
    )
    # Labels carry meaning; list-number formatting does not. Accept complete
    # unnumbered headings while retaining order/substance/length checks.
    heading_numbers = {label[3:]: label for labels in label_sets for label in labels}
    normalized = []
    for line in text.splitlines():
        heading = line.strip().strip("#* ").rstrip(":").translate(str.maketrans(
            {"\u2010": "-", "\u2011": "-", "\u2013": "-", "\u2014": "-"}))
        normalized.append(heading_numbers.get(heading, line))
    text = "\n".join(normalized)
    for labels in label_sets:
        start = text.rfind(labels[0])
        if start >= 0:
            tail = text[start:]
            positions = [tail.find(label) for label in labels]
            if (all(position >= 0 for position in positions) and
                    positions == sorted(positions) and
                    all(tail[position + len(label):end].strip(" :\n\t*")
                        for position, label, end in zip(
                            positions, labels, positions[1:] + [len(tail)])) and
                    len(tail) <= max_chars and
                    len(tail.split()) <= 1100):
                return tail
    return (
        "[INCOMPLETE INVESTIGATOR OUTPUT OMITTED BY CONTROLLER: it did not "
        "supply all five ordered conclusions within the bounded response. Do "
        "not infer a patch from its unfinished trajectory. Derive the edit "
        "from DYNAMIC CAUSAL EVIDENCE and the AUTHORITATIVE MECHANICAL "
        "OPCODE-TO-C BRIDGE below.]"
    )


def diagnosis_complete(text: str, metadata: dict) -> bool:
    """A token-capped reasoning trajectory is not a completed patch hypothesis."""
    return (metadata.get("done_reason") not in {"length", "error"}
            and not metadata.get("_fell_back_to_thinking")
            and not _compact_diagnosis(text).startswith("[INCOMPLETE"))


def diagnosis_completion_prompt(source: str, draft: str, feedback: str) -> str:
    return (
        "Finish ONE bounded diagnosis. The previous investigation did not finish. "
        "Do not continue its chain of thought. Check CURRENT C against the mechanical "
        "evidence and state one testable old-to-new edit, or explicitly state that "
        "the evidence is insufficient. No patch JSON yet. Use these five labels "
        "in order, with at most 100 words each:\n"
        "1. NEXT BAD OBSERVABLE\n2. TARGET VALUE/ADDRESS PROVENANCE\n"
        "3. CANDIDATE VALUE/ADDRESS PROVENANCE\n4. SOURCE-LEVEL CAUSE\n"
        "5. MINIMAL PATCH PLAN\n\nCURRENT C:\n" + _number_source(source)
        + "\n\nMECHANICAL EVIDENCE:\n" + feedback[:12000]
        + "\n\nUNFINISHED DRAFT (untrusted hypothesis, not a conclusion):\n"
        + draft[-4000:])


def build_patch_prompt(
        source: str, diagnosis: str,
        results: list[differential.DifferentialResult],
        rejected: list[str], diff: str = "",
        divergence_bundle: str = "",
        exactness_history: tuple[dict, ...] | list[dict] = ()) -> str:
    semantics_pass = all(row.status == "passed" for row in results)
    observed_clean = _observed_semantics_clean(results)
    feedback = _prioritized_feedback(results, source, divergence_bundle)
    gradient = exactness_gradient.build(
        diff, source, history=exactness_history, rejected=rejected)
    if observed_clean and gradient.residual_classification.startswith(
            ("register-operand-only", "register-or-local-order-only")):
        return EXACTNESS_PATCH_PROMPT.format(
            max_span_lines=MAX_SOURCE_SPAN_LINES,
            exactness_feed=gradient.render(), source=_number_source(source))
    compact_diagnosis = _compact_diagnosis(diagnosis)
    if compact_diagnosis.startswith("[INCOMPLETE INVESTIGATOR OUTPUT"):
        # A token-capped, repetitive investigation is negative evidence, not
        # useful context. Keep controller facts salient for the patch phase.
        feedback = (next_bad_observable_ledger(results, source) + "\n\n" +
                    semantic_gradient.render_operation_gradient(
                        results, source))
        feedback = "\n".join(
            f"first divergence: {row.first_divergence}"
            for row in results if row.first_divergence)[:2000] + "\n\n" + feedback
        if divergence_bundle and len(divergence_bundle) <= 6000:
            feedback += "\n\n" + divergence_bundle
    return PATCH_PROMPT.format(
        kinds=", ".join(sorted(modelrepair.KINDS)),
        max_span_lines=MAX_SOURCE_SPAN_LINES, source=_number_source(source),
        diagnosis=compact_diagnosis,
        feedback=feedback,
        mechanical_bridge=mechanical_opcode_to_c_bridge(source, feedback),
        principles=exactness_principles(diff, source),
        exactness_feed=gradient.render(),
        objective=(
            "- Every finite semantic case already passes. Preserve that full "
            "contract; byte exactness is now the primary goal."
            if semantics_pass else
            ("- Every target-observable semantic case passes; target-side "
             "execution-limit cases remain coverage debt. Preserve the "
             "passing contract and make only compiler/source-shape edits. If "
             "the residual is register-operand-only, do not change literals, "
             "pointer offsets, array indices, or control predicates; physical "
             "register names do not establish a pointer/value bug."
             if observed_clean else
             "- Semantic behavior is the primary goal; byte exactness remains "
             "a shadow metric until all cases pass.")),
        rejected=("\n".join(f"- {item}" for item in rejected[-6:])
                  if rejected else "(none)"))


def build_patch_retry_prompt(
        source: str, response: str, error: str,
        diagnosis: str = "", feedback: str = "", diff: str = "",
        exactness_history: tuple[dict, ...] | list[dict] = (),
        rejected: tuple[str, ...] | list[str] = ()) -> str:
    gradient = exactness_gradient.build(
        diff, source, history=exactness_history, rejected=rejected)
    if gradient.residual_classification.startswith(
            ("register-operand-only", "register-or-local-order-only")):
        retry_action = (
            "Complete the same selected value-epoch split. Keep the old "
            "local declaration, add a distinct same-typed local beside it, "
            "and rename the selected epoch's definition and every required "
            "use. Do not switch to an unrelated local named in a schema."
            if error.startswith("incomplete value-epoch split:") else
            "Discard the rejected edit instead of repairing or repeating "
            "it. Choose one different, still-untried lifetime, scope, "
            "value-epoch, temporary, or equivalent CFG-shape experiment "
            "from the authoritative feed."
        )
        return EXACTNESS_PATCH_RETRY_PROMPT.format(
            error=error, retry_action=retry_action,
            exactness_feed=gradient.render(),
            max_span_lines=MAX_SOURCE_SPAN_LINES,
            source=_number_source(source))
    return PATCH_RETRY_PROMPT.format(
        error=error, response=response[:12000],
        diagnosis=_compact_diagnosis(diagnosis), feedback=feedback[:16000],
        mechanical_bridge=mechanical_opcode_to_c_bridge(source, feedback),
        exactness_feed=gradient.render(),
        max_span_lines=MAX_SOURCE_SPAN_LINES, source=_number_source(source))


def build_compiler_fix_prompt(
        source: str, compiler_error: str, diagnosis: str) -> str:
    return COMPILER_FIX_PROMPT.format(
        source=_number_source(source),
        max_span_lines=MAX_SOURCE_SPAN_LINES,
        compiler_error=compiler_error[:6000],
        diagnosis=diagnosis[:12000])


def build_prompt(assembly: str, source: str, attempt: workspace.Attempt,
                 results: list[differential.DifferentialResult],
                 rejected: list[str]) -> str:
    """Backward-compatible single prompt used only by light unit callers."""
    diagnosis = build_diagnosis_prompt(
        assembly, "(candidate assembly supplied at runtime)", source,
        attempt, results, rejected)
    return build_patch_prompt(source, diagnosis, results, rejected)


def _call_prefix(row: differential.DifferentialResult) -> int:
    count = 0
    for left, right in zip(row.target.calls, row.candidate.calls):
        if (left.callee, left.arguments) != (right.callee, right.arguments):
            break
        count += 1
    return count


def _checkpoint_prefix(row: differential.DifferentialResult) -> int:
    count = 0
    for left, right in zip(row.target.calls, row.candidate.calls):
        if (left.callee, left.arguments, left.checkpoint_digest, left.returned) != \
                (right.callee, right.arguments,
                 right.checkpoint_digest, right.returned):
            break
        count += 1
    return count


def _write_prefix(row: differential.DifferentialResult) -> int:
    count = 0
    for left, right in zip(row.target.writes, row.candidate.writes):
        if (left.address, left.width, left.value) != \
                (right.address, right.width, right.value):
            break
        count += 1
    return count


def _different_bytes(row: differential.DifferentialResult) -> int:
    names = set(row.target.persistent_state) | set(row.candidate.persistent_state)
    return sum(row.target.persistent_state.get(name) !=
               row.candidate.persistent_state.get(name) for name in names)


def _symbolic_pointer_offset(pointer: str) -> tuple[str, int] | None:
    match = re.fullmatch(
        r"(?P<base>.+?)(?:\+0x(?P<offset>[0-9a-fA-F]+))?", pointer)
    if match is None:
        return None
    return match.group("base"), int(match.group("offset") or "0", 16)


def _write_observable_distance(row: differential.DifferentialResult) -> int:
    """A late semantic gradient for aligned writes that are already similar.

    Exact gates still decide success.  This only distinguishes otherwise tied
    failing candidates, such as the same halfword stored six versus four bytes
    away from the target.  Symbolic regions must agree, so unrelated pointers
    never become "closer" merely because their synthetic addresses do.
    """
    distance = 0
    count = max(len(row.target.writes), len(row.candidate.writes))
    for index in range(count):
        if index >= len(row.target.writes) or \
                index >= len(row.candidate.writes):
            distance += 0x10000
            continue
        left = row.target.writes[index]
        right = row.candidate.writes[index]
        left_pointer = _symbolic_pointer_offset(left.address)
        right_pointer = _symbolic_pointer_offset(right.address)
        if left_pointer is None or right_pointer is None or \
                left_pointer[0] != right_pointer[0]:
            address_distance = 0x10000
        else:
            address_distance = abs(left_pointer[1] - right_pointer[1])
        width_distance = abs(left.width - right.width) * 0x100
        common_bits = min(left.width, right.width) * 8
        mask = (1 << common_bits) - 1 if common_bits else 0
        value_distance = ((left.value ^ right.value) & mask).bit_count()
        distance += address_distance + width_distance + value_distance
    return distance


def _next_write_observable_progress(
        row: differential.DifferentialResult) -> tuple[int, ...]:
    """Rank independently corrected operands at the first unequal write.

    A repair can make the causal store address exact while its value is still
    wrong. That is forward progress even when writing to the right location
    temporarily exposes more final-memory differences than repeatedly
    overwriting the wrong location. Keep this local gradient ahead of global
    memory distance, while the exact differential gates remain authoritative.
    """
    index = _write_prefix(row)
    if index >= len(row.target.writes) or index >= len(row.candidate.writes):
        return (0, 0, 0, 0, -0x10000, -0x10000, -0x10000)

    left = row.target.writes[index]
    right = row.candidate.writes[index]
    left_pointer = _symbolic_pointer_offset(left.address)
    right_pointer = _symbolic_pointer_offset(right.address)
    same_region = (
        left_pointer is not None and right_pointer is not None and
        left_pointer[0] == right_pointer[0])
    if same_region:
        address_distance = abs(left_pointer[1] - right_pointer[1])
    else:
        address_distance = 0x10000
    width_distance = abs(left.width - right.width)
    common_bits = min(left.width, right.width) * 8
    mask = (1 << common_bits) - 1 if common_bits else 0
    value_distance = ((left.value ^ right.value) & mask).bit_count()
    address_exact = int(left.address == right.address)
    width_exact = int(left.width == right.width)
    value_exact = int(left.value == right.value)
    return (
        address_exact + width_exact + value_exact,
        address_exact,
        width_exact,
        value_exact,
        -address_distance,
        -width_distance,
        -value_distance,
    )


def _next_write_progress_key(
        results: list[differential.DifferentialResult]) -> tuple[int, ...]:
    rows = [_next_write_observable_progress(row) for row in results]
    return tuple(sum(row[index] for row in rows)
                 for index in range(7))


def behavior_key(results: list[differential.DifferentialResult],
                 attempt: workspace.Attempt) -> tuple:
    """Rank semantic progress without allowing byte score to lead it."""
    return (
        sum(row.status == "passed" for row in results),
        sum(row.status != "inconclusive" for row in results),
        sum(_checkpoint_prefix(row) for row in results),
        sum(_call_prefix(row) for row in results),
        -sum(_different_bytes(row) for row in results),
        sum(_write_prefix(row) for row in results),
        -sum(_write_observable_distance(row) for row in results),
        -sum(abs(row.target.instruction_count - row.candidate.instruction_count)
             for row in results),
        float(attempt.score),
    )


def acceptance_key(results: list[differential.DifferentialResult],
                   attempt: workspace.Attempt) -> tuple:
    """Require observed semantic progress until the semantic gate is closed.

    Candidate instruction count and object similarity are useful exactness
    gradients only after behavior passes. Before then they can reward deleting
    unrelated statements while the same fault remains.
    """
    key = behavior_key(results, attempt)
    if _observed_semantics_clean(results):
        # Once the semantic gate is closed, diagnostics must not replace a
        # better byte match with a lower-scoring source. Neutral alternatives
        # remain reachable through the separate bounded frontier.
        return (int(attempt.exact), 1, float(attempt.score)) + key
    causal = (
        key[0],
        key[1],
        key[2],
        key[3],
        sum(_write_prefix(row) for row in results),
        _next_write_progress_key(results),
    )
    has_paired_bad_write = any(
        (index := _write_prefix(row)) < len(row.target.writes) and
        index < len(row.candidate.writes)
        for row in results)
    if has_paired_bad_write:
        return (int(attempt.exact), 0) + causal
    return (int(attempt.exact), 0) + causal + (
        -sum(_different_bytes(row) for row in results),
        -sum(_write_observable_distance(row) for row in results),
    )


def preserves_verified_prefix(
        current: list[differential.DifferentialResult],
        child: list[differential.DifferentialResult]) -> bool:
    """A repair may expose later faults but may not regress confirmed prefixes."""
    if len(current) != len(child):
        return False
    for old, new in zip(current, child):
        if _target_inconclusive(old):
            continue
        if new.status == "inconclusive" or \
                (old.candidate.status == "returned" and
                 new.candidate.status != "returned") or \
                _checkpoint_prefix(new) < _checkpoint_prefix(old) or \
                _call_prefix(new) < _call_prefix(old):
            return False
    return True


def _receipt_result(row: differential.DifferentialResult) -> dict:
    """Do not persist a 10,000-event trace for a target-limited test case."""
    if not _target_inconclusive(row):
        return row.to_dict()
    return {
        "case": row.case,
        "status": row.status,
        "reasons": list(row.reasons),
        "first_divergence": row.first_divergence,
        "target": {
            "status": row.target.status,
            "instruction_count": row.target.instruction_count,
            "error": row.target.error,
        },
        "candidate": {
            "status": row.candidate.status,
            "instruction_count": row.candidate.instruction_count,
            "error": row.candidate.error,
        },
        "trace_omitted": "target did not reach observable completion",
    }


def _differential_summary(
        results: list[differential.DifferentialResult]) -> dict:
    examples = [row for row in results if row.status != "passed"][:8]
    if not examples:
        examples = results[:8]
    return {
        "case_count": len(results),
        "passed": sum(row.status == "passed" for row in results),
        "failed": sum(row.status == "failed" for row in results),
        "inconclusive": sum(row.status == "inconclusive" for row in results),
        "first_divergences": [
            row.first_divergence for row in results
            if row.status != "passed"
        ][:16],
        "feedback": [differential.repair_feedback(row) for row in examples],
        "recorded_result_count": len(examples),
        "results": [_receipt_result(row) for row in examples],
    }


def _attempt_summary(attempt: workspace.Attempt) -> dict:
    return {
        "attempt_id": attempt.receipt_id,
        "compiled": bool(attempt.compiled),
        "score": float(attempt.score),
        "exact": bool(attempt.exact),
        "compiler_stderr": (attempt.compiler_stderr or "")[:3000],
        "verification": attempt.verification,
    }


def semantic_certificate(target: str, candidate: str, source: str,
                         cases: tuple[differential.TestCase, ...],
                         results: list[differential.DifferentialResult], *,
                         call_arities: dict[str, int],
                         return_registers: tuple[str, ...]) -> dict:
    """Bind sampled agreement/coverage to this exact candidate and environment."""
    if len(cases) != len(results) or any(case.name != row.case for case, row in zip(cases, results)):
        raise ValueError("semantic results do not correspond to the certified panel")
    target_coverage = differential.coverage_report(
        differential.Program.parse("target", target), [r.target for r in results]).to_dict()
    candidate_coverage = differential.coverage_report(
        differential.Program.parse("candidate", candidate), [r.candidate for r in results]).to_dict()
    return {
        "kind": "sampled_semantic_certificate", "schema_version": 1,
        "universal_equivalence_proven": False,
        "source_sha256": _sha(source), "target_assembly_sha256": _sha(target),
        "candidate_assembly_sha256": _sha(candidate),
        "runner_sha256": _sha(Path(differential.__file__).read_text(encoding="utf-8")),
        "cases_sha256": _sha(json.dumps([asdict(c) for c in cases], sort_keys=True)),
        "call_arities": call_arities, "return_registers": list(return_registers),
        "environment": "synthetic memory and opaque-call stubs; interpreter default execution bound",
        "cases": len(cases), "all_cases_passed": bool(results) and all(r.status == "passed" for r in results),
        "target_coverage": target_coverage, "candidate_coverage": candidate_coverage,
    }


def _evaluate(target_assembly: str, candidate_assembly: str, *,
              cases: tuple[differential.TestCase, ...],
              call_arities: dict[str, int],
              return_registers: tuple[str, ...]
              ) -> list[differential.DifferentialResult]:
    return differential.run_suite(
        target_assembly, candidate_assembly, cases,
        target_name="target", candidate_name="gpt-oss-candidate",
        call_arities=call_arities, return_registers=return_registers)


def _resynchronize(target_assembly: str, candidate_assembly: str, *,
                   cases: tuple[differential.TestCase, ...],
                   call_arities: dict[str, int],
                   return_registers: tuple[str, ...]
                   ) -> tuple[list[differential.ResynchronizationReport], str]:
    """Build explanation-only later-fault evidence for the current candidate."""
    reports = differential.run_resynchronized_suite(
        target_assembly, candidate_assembly, cases,
        target_name="target", candidate_name="gpt-oss-candidate",
        call_arities=call_arities, return_registers=return_registers,
        max_divergences=3)
    return reports, differential.render_divergence_bundle(reports)


def _resynchronization_summary(
        reports: list[differential.ResynchronizationReport],
        bundle: str) -> dict:
    return {
        "authority": "diagnostic-only; ordinary unintervened run is the gate",
        "bundle_sha256": _sha(bundle),
        "independent_observations": sum(
            len(report.observations) for report in reports),
        "reports": [report.to_dict() for report in reports],
    }


def run(*, repo: Path, db: Path, source_path: Path,
        source_parent_attempt_id: int, output: Path, best_source_out: Path,
        model: str, endpoint: str, rounds: int, timeout: int, think: str,
        num_thread: int, temperature: float, diagnosis_num_predict: int,
        patch_num_predict: int, patch_retries: int, compiler_retries: int,
        max_stalls: int, seed: int, cache_dir: Path | None,
        function: str = FUNCTION,
        cases: tuple[differential.TestCase, ...] | None = None,
        call_arities: dict[str, int] | None = None,
        return_registers: tuple[str, ...] = (),
        deterministic_only: bool = False,
        proposal_replay_budget: int = 8,
        proposal_cutoff: int | None = None,
        dependency_context: str = "",
        exactness_expansions: int = 4,
        compiler_response_policy: transition_policy.TransitionPolicy | None =
        None) -> dict:
    from solver.semantic_replay_cache import ReplayCache
    replay_cache = ReplayCache(_evaluate)
    evaluate = replay_cache.evaluate
    if not 1 <= exactness_expansions <= 16:
        raise ValueError('exactness_expansions must be between 1 and 16')
    if cases is None:
        if function != FUNCTION:
            raise ValueError(
                "non-mode16 differential repair requires explicit cases")
        cases = differential.mode16_cases()
    if not cases:
        raise ValueError("differential repair requires at least one case")
    if call_arities is None:
        call_arities = (dict(differential.MODE16_CALL_ARITIES)
                        if function == FUNCTION else {})
    source = source_path.read_text(encoding="utf-8")
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    # Freeze the response model before this run creates any new attempts.  Its
    # estimates also exclude every historical transition for the target
    # function, so ranking measures transfer rather than memorization.
    if compiler_response_policy is None:
        compiler_response_policy = transition_policy.TransitionPolicy.from_db(
            conn)
    policy_report = compiler_response_policy.summary(
        exclude_function=function)
    ws = workspace.bootstrap(repo, function)
    raw_target = workspace.target_asm(ws, function)
    project_context = project_headers.prompt_context(
        repo, function, raw_target, source)
    project_context += dependency_context
    normalized_target = workspace.semantic_assembly(
        (ws / "target_object_dump_normalized.s").read_text(
            errors="replace"), ws / "target.o")
    run_id = f"differential-repair-{time.time_ns()}-{function}"
    config = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "behavior_key_version": BEHAVIOR_KEY_VERSION,
        "kind": "differential-debugger-repair",
        "function": function,
        "semantic_case_count": len(cases),
        "exactness_expansions": exactness_expansions,
        "semantic_cases_sha256": _sha(json.dumps(
            [asdict(case) for case in cases], sort_keys=True)),
        "call_arities": dict(sorted(call_arities.items())),
        "return_registers": list(return_registers),
        "evaluation_regime": "development target; not held out",
        "target_reference_c_available": False,
        "feedback_source": "target binary differential execution",
        "operation_gradient": (
            "path-conditioned dynamic value DAGs aggregated across every "
            "semantic case"),
        "diagnostic_case_limit": 8,
        "max_source_spans": MAX_SOURCE_SPANS,
        "max_source_span_lines": MAX_SOURCE_SPAN_LINES,
        "resynchronization": {
            "max_independent_divergences_per_case": 3,
            "interventions": "call checkpoint or aligned persistent write",
            "authority": "diagnostic-only; never accepted as a semantic pass",
        },
        "model": model,
        "rounds": rounds,
        "timeout": timeout,
        "think": think,
        "num_thread": num_thread,
        "temperature": temperature,
        "diagnosis_num_predict": diagnosis_num_predict,
        "patch_num_predict": patch_num_predict,
        "patch_retries": patch_retries,
        "compiler_retries": compiler_retries,
        "max_stalls": max_stalls,
        "seed": seed,
        "cache_dir": str(cache_dir) if cache_dir else None,
        "selection": (
            "semantic progress until the observed semantic gate closes; "
            "then exactness and byte score before diagnostic runtime shape"),
        "deterministic_exactness": (
            "after observed semantics pass, generate bounded loop, branch, "
            "conditional-return and indexing code shapes regardless of residual "
            "class; include target-backed representation repairs, typed expression "
            "materialization, split-epoch, statement-order and declaration/lifetime "
            "experiments for structural and allocation residuals; add counted-loop, "
            "quotient-lifetime and masked-parameter source idioms, reserving up "
            "to eight candidates for pairs of disjoint edits; rank direct "
            "compiler sites before truncation and permit at most two expanded "
            "source representatives per assembly output; "
            "rank it using frozen leave-one-function-out compiler-response "
            "transitions, finish each sibling panel before expanding its "
            "best candidate (stop early only on exactness), record "
            "register-cycle movement for each compile, "
            "then bypass free-form diagnosis before constrained source-shape "
            "actuation"),
        "compiler_response_policy": {
            "model_version": transition_policy.MODEL_VERSION,
            "training_transition_count": policy_report["transition_count"],
            "training_function_count": policy_report["function_count"],
            "action_family_count": policy_report["action_family_count"],
            "excluded_function": function,
            "frozen_before_run": True,
            "objective": (
                "lexicographic exact/vector response; byte score is telemetry, "
                "not a scalar training loss"),
            "authority": policy_report["authority"],
        },
        "deterministic_semantic_search": (
            "compile controller-derived indexed-store alternatives only when "
            "the executed byte equation and C element size determine them"),
        "deterministic_only": deterministic_only,
        "terminal_success": "verifier exact=true only",
        "proposal_replay_budget": proposal_replay_budget,
        "proposal_cutoff": proposal_cutoff,
    }
    root_tag = f"{function}_differential_repair_root_{time.time_ns()}"
    root = workspace.score(
        ws, repo, root_tag, source, conn=conn, func=function, iteration=0,
        strategy="differential-repair-root", model=model, run_id=run_id,
        parent_attempt_id=source_parent_attempt_id,
        relation="differential-repair-root", action="fresh root verification",
        run_kind="differential-debugger-repair", run_config=config)
    if not root.compiled:
        raise RuntimeError(f"frozen differential root stopped compiling: "
                           f"{root.compiler_stderr}")
    root_assembly = workspace.semantic_assembly(
        (ws / f"{root_tag}_object_dump_normalized.s").read_text(
            errors="replace"), ws / f"{root_tag}.o")
    root_results = evaluate(
        normalized_target, root_assembly, cases=cases,
        call_arities=call_arities, return_registers=return_registers)
    root_resynchronization, root_divergence_bundle = _resynchronize(
        normalized_target, root_assembly,
        cases=_diagnostic_cases(cases, root_results),
        call_arities=call_arities, return_registers=return_registers)
    root_divergence_bundle = _operation_feedback(
        root_results, source, root_divergence_bundle)
    best_source, best_attempt = source, root
    best_results, best_tag = root_results, root_tag
    best_resynchronization = root_resynchronization
    best_divergence_bundle = root_divergence_bundle
    active_source, active_attempt = source, root
    active_results = root_results
    active_assembly = root_assembly
    active_resynchronization = root_resynchronization
    active_divergence_bundle = root_divergence_bundle
    rejected: list[str] = []
    iterations: list[dict] = []
    deterministic_iterations: list[dict] = []
    prior_exactness_history = list(exactness_gradient.receipt_history(
        conn, source_parent_attempt_id, source))
    deterministic_seen = ({_sha(source)} | {
        str(row.get("source_sha256")) for row in prior_exactness_history
        if row.get("source_sha256")
    })
    charged_tokens = recorded_tokens = 0
    consecutive_stalls = 0
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "kind": "differential-debugger-repair",
        "run_id": run_id,
        "created_at": int(time.time()),
        "status": "running",
        "config": config,
        "inputs": {
            "source_path": str(source_path),
            "source_sha256": _sha(source),
            "source_parent_attempt_id": source_parent_attempt_id,
            "prior_exactness_experiment_count": len(
                prior_exactness_history),
            "target_assembly_sha256": _sha(normalized_target),
        },
        "root": {
            "attempt": _attempt_summary(root),
            "behavior_key": list(behavior_key(root_results, root)),
            "differential": _differential_summary(root_results),
            "exactness_gradient": exactness_gradient.build(
                root.diff, source,
                history=prior_exactness_history).to_dict(),
            "resynchronization": _resynchronization_summary(
                root_resynchronization, root_divergence_bundle),
        },
        "compiler_response_policy": policy_report,
        "deterministic_exactness": deterministic_iterations,
        "iterations": iterations,
    }
    _atomic_json(output, receipt)
    termination_reason = "round limit"
    try:
        # Repair controller defects without asking the model to rediscover a
        # correct historical edit. Keep raw and normalized source variants;
        # neither a normalizer nor a historical verdict is a correctness oracle.
        recovered_rows = receipt["proposal_recovery"] = []
        recovery_seen = {_sha(source)}
        recovery_probes = 0
        saved_proposals = (proposal_recovery.load(
            conn, function, source, cutoff=proposal_cutoff)
            if proposal_replay_budget > 0 and not root.exact else ())
        for saved in saved_proposals:
            if recovery_probes >= proposal_replay_budget or best_attempt.exact:
                break
            try:
                _proposal, raw_source, _format = parse_and_apply_patch(saved.response, saved.source)
            except ValueError:
                continue
            for transform, candidate_source in (("raw", raw_source), ("c89", c89.to_c89(raw_source))):
                if recovery_probes >= proposal_replay_budget or best_attempt.exact:
                    break
                candidate_hash = _sha(candidate_source)
                if candidate_hash in recovery_seen:
                    continue
                recovery_seen.add(candidate_hash)
                recovered = {"proposal_id": saved.proposal_id,
                             "parent_attempt_id": saved.parent_attempt_id,
                             "transform": transform, "source_sha256": candidate_hash,
                             "accepted": False}
                try:
                    workspace.assert_uncontaminated(candidate_source, repo, function)
                    if saved.source == source:
                        validate_observable_locks(source, candidate_source, root_results)
                except ValueError as exc:
                    recovered.update(status="rejected-before-compile", error=str(exc))
                    recovered_rows.append(recovered)
                    continue
                recovery_probes += 1
                tag = f"{function}_proposal_replay_{saved.proposal_id}_{transform}_{time.time_ns()}"
                child = workspace.score(
                    ws, repo, tag, candidate_source, conn=conn, func=function,
                    strategy="automatic-proposal-recovery", model="", run_id=run_id,
                    parent_attempt_id=saved.parent_attempt_id,
                    relation="replay-model-proposal", action=f"replay {transform} proposal {saved.proposal_id}",
                    run_kind="differential-debugger-repair", run_config=config,
                    extra={"replayed_model_proposal_id": saved.proposal_id,
                           "source_transform": transform})
                recovered.update(status="tested", attempt=_attempt_summary(child))
                if child.compiled:
                    assembly = workspace.semantic_assembly(
                        (ws / f"{tag}_object_dump_normalized.s").read_text(errors="replace"), ws / f"{tag}.o")
                    results = evaluate(normalized_target, assembly, cases=cases,
                                        call_arities=call_arities, return_registers=return_registers)
                    accepted = (preserves_verified_prefix(best_results, results)
                                and acceptance_key(results, child) > acceptance_key(best_results, best_attempt))
                    recovered.update(differential=_differential_summary(results), accepted=accepted)
                    if accepted:
                        best_source, best_attempt, best_results, best_tag = candidate_source, child, results, tag
                        active_source, active_attempt = candidate_source, child
                        active_results, active_assembly = results, assembly
                        best_resynchronization, best_divergence_bundle = _resynchronize(
                            normalized_target, assembly, cases=_diagnostic_cases(cases, results),
                            call_arities=call_arities, return_registers=return_registers)
                        best_divergence_bundle = _operation_feedback(results, candidate_source, best_divergence_bundle)
                        active_resynchronization = best_resynchronization
                        active_divergence_bundle = best_divergence_bundle
                        artifact = output.with_name(f"{output.stem}.recovered-{saved.proposal_id}-{transform}.c")
                        artifact.write_text(candidate_source, encoding="utf-8")
                        recovered["source_path"] = str(artifact)
                recovered_rows.append(recovered)
                _atomic_json(output, receipt)
        for round_index in range(1, rounds + 1):
            if active_attempt.exact:
                termination_reason = "byte exact"
                break

            semantic_advanced = False
            semantic_variants = [
                variant for variant in (
                    *control_evidence.source_variants(active_results, active_source),
                    *deterministic_semantic_candidates(active_source, active_divergence_bundle, function))
                if _sha(variant.source) not in deterministic_seen
            ]
            for variant_index, variant in enumerate(
                    semantic_variants, start=1):
                deterministic_seen.add(_sha(variant.source))
                tag = (
                    f"{function}_differential_semantic_principle_"
                    f"{round_index}_{variant_index}_{time.time_ns()}")
                started = time.time()
                child = workspace.score(
                    ws, repo, tag, variant.source, conn=conn,
                    func=function, iteration=round_index,
                    strategy="differential-deterministic-semantic-principle",
                    model="", run_id=run_id,
                    parent_attempt_id=active_attempt.receipt_id,
                    relation="deterministic-semantic-search",
                    action=variant.label, feedback=active_divergence_bundle,
                    run_kind="differential-debugger-repair",
                    run_config=config,
                    extra={"phase": "deterministic-semantic-principle",
                           "round": round_index,
                           "variant": variant_index})
                deterministic_row = {
                    "phase": "semantic-principle",
                    "round": round_index,
                    "variant": variant_index,
                    "label": variant.label,
                    "source_sha256": _sha(variant.source),
                    "attempt": _attempt_summary(child),
                    "wall_ms": int((time.time() - started) * 1000),
                    "accepted_for_next_round": False,
                }
                if child.compiled:
                    child_assembly = workspace.semantic_assembly(
                        (ws / f"{tag}_object_dump_normalized.s").read_text(
                            errors="replace"), ws / f"{tag}.o")
                    child_results = evaluate(
                        normalized_target, child_assembly, cases=cases,
                        call_arities=call_arities,
                        return_registers=return_registers)
                    child_resynchronization, child_bundle = _resynchronize(
                        normalized_target, child_assembly,
                        cases=_diagnostic_cases(cases, child_results),
                        call_arities=call_arities,
                        return_registers=return_registers)
                    child_bundle = _operation_feedback(
                        child_results, variant.source, child_bundle)
                    child_key = behavior_key(child_results, child)
                    prefix_preserved = preserves_verified_prefix(
                        active_results, child_results)
                    accepted = (
                        prefix_preserved and acceptance_key(
                            child_results, child) > acceptance_key(
                            active_results, active_attempt))
                    deterministic_row.update({
                        "behavior_key": list(child_key),
                        "differential": _differential_summary(child_results),
                        "verified_prefix_preserved": prefix_preserved,
                        "accepted_for_next_round": accepted,
                    })
                    if accepted:
                        active_source, active_attempt = variant.source, child
                        active_results, active_assembly = (
                            child_results, child_assembly)
                        active_resynchronization = child_resynchronization
                        active_divergence_bundle = child_bundle
                        consecutive_stalls = 0
                        semantic_advanced = True
                        if acceptance_key(
                                child_results, child) > acceptance_key(
                                    best_results, best_attempt):
                            best_source, best_attempt = variant.source, child
                            best_results, best_tag = child_results, tag
                            best_resynchronization = child_resynchronization
                            best_divergence_bundle = child_bundle
                        artifact = output.with_name(
                            f"{output.stem}.round{round_index}."
                            f"semantic-principle{variant_index}.c")
                        artifact.write_text(
                            variant.source, encoding="utf-8")
                        deterministic_row["source_path"] = str(artifact)
                        rejected.append(
                            "VERIFIED ACCEPTED CONTRACT deterministic round "
                            f"{round_index}: retained `{variant.label}` after "
                            "compile and full differential verification.")
                deterministic_iterations.append(deterministic_row)
                _atomic_json(output, receipt)
                if semantic_advanced:
                    break
            if active_attempt.exact:
                termination_reason = "byte exact after deterministic semantic search"
                break
            if semantic_advanced:
                continue

            # Once observed behavior is closed, search alternative code shapes
            # for every residual class before another model call. Allocation
            # generators retain their narrower gates. Re-run the semantic
            # suite on every compiling candidate and compose clean alternatives.
            search_wave = 0
            frontier = candidate_frontier.Frontier(width=4, representatives_per_object=2)
            frontier.offer(candidate_frontier.Candidate(
                active_source, active_attempt, active_results, active_assembly,
                "root", acceptance_key(active_results, active_attempt), "root"))
            while (_observed_semantics_clean(active_results)
                   and not active_attempt.exact and search_wave < exactness_expansions):
                parent = frontier.pop()
                if parent is None:
                    break
                search_wave += 1
                policy_state = transition_policy.residual_state(
                    parent.attempt.diff, parent.source,
                    score=parent.attempt.score,
                    exact=parent.attempt.exact)
                source_mapping = residual_sites.source_map(
                    parent.source, function, parent.attempt.diff, parent.attempt.source_attribution)
                source_history = residual_sites.load(
                    conn, parent.source, parent.attempt.diff)
                source_mapping["compiler_interventions"] = [
                    row["source_intervention"] for row in source_history]
                receipt.setdefault("mismatch_source_maps", []).append({
                    "parent_attempt_id": parent.attempt.receipt_id,
                    "round": round_index, "search_wave": search_wave,
                    **source_mapping})
                variants = [
                    variant for variant in deterministic_exactness_candidates(
                        parent.source, function, parent.attempt.diff,
                        policy=compiler_response_policy,
                        source_history=source_history,
                        direct_attribution=parent.attempt.source_attribution)
                    if _sha(variant.source) not in deterministic_seen
                ]
                if not variants:
                    continue
                accepted_variant = False
                panel_source, panel_attempt = parent.source, parent.attempt
                panel_results = parent.results
                wave_outputs: set[str] = set()
                wave_compiled = wave_tested = wave_clean = wave_improved = 0
                parent_assembly_sha = _sha(parent.assembly)
                for variant_index in range(1, len(variants) + 1):
                    # Re-rank the remaining siblings with this panel's actual
                    # compiler responses. Stable ties preserve policy order.
                    variant = min(variants, key=lambda candidate: residual_sites.rank(
                        panel_source, candidate, source_mapping, source_history))
                    variants.remove(variant)
                    deterministic_seen.add(_sha(variant.source))
                    parent_gradient = exactness_gradient.build(
                        panel_attempt.diff, panel_source,
                        history=(prior_exactness_history +
                                 deterministic_iterations),
                        rejected=rejected)
                    tag = (
                        f"{function}_differential_statement_order_"
                        f"{round_index}_{search_wave}_{variant_index}_"
                        f"{time.time_ns()}")
                    started = time.time()
                    child = workspace.score(
                        ws, repo, tag, variant.source, conn=conn,
                        func=function, iteration=round_index,
                        strategy="differential-deterministic-statement-order",
                        model="", run_id=run_id,
                        parent_attempt_id=panel_attempt.receipt_id,
                        relation="deterministic-exactness-search",
                        action=variant.label, feedback=panel_attempt.diff,
                        run_kind="differential-debugger-repair",
                        run_config=config,
                        extra={"phase": "deterministic-exactness",
                               "round": round_index,
                               "search_wave": search_wave,
                               "variant": variant_index})
                    deterministic_row = {
                        "round": round_index,
                        "search_wave": search_wave,
                        "variant": variant_index,
                        "label": variant.label,
                        "source_sha256": _sha(variant.source),
                        "compiler_response_estimate":
                            compiler_response_policy.estimate(
                                variant.label, policy_state,
                                relation="deterministic-exactness-search",
                                exclude_function=function).to_dict(),
                        "attempt": _attempt_summary(child),
                        "wall_ms": int((time.time() - started) * 1000),
                        "accepted_for_next_round": False,
                    }
                    child_semantic_clean = False
                    wave_tested += 1
                    if child.compiled:
                        child_gradient = exactness_gradient.build(
                            child.diff, variant.source,
                            history=(prior_exactness_history +
                                     deterministic_iterations),
                            rejected=rejected)
                        deterministic_row["register_role_movement"] = \
                            exactness_gradient.movement(
                                parent_gradient, child_gradient)
                        child_assembly = workspace.semantic_assembly(
                            (ws / f"{tag}_object_dump_normalized.s").read_text(
                                errors="replace"), ws / f"{tag}.o")
                        wave_compiled += 1
                        assembly_sha = _sha(child_assembly)
                        wave_outputs.add(assembly_sha)
                        deterministic_row["candidate_assembly_sha256"] = assembly_sha
                        deterministic_row["parent_assembly_sha256"] = parent_assembly_sha
                        child_results = evaluate(
                            normalized_target, child_assembly, cases=cases,
                            call_arities=call_arities,
                            return_registers=return_registers)
                        child_key = behavior_key(child_results, child)
                        child_semantic_clean = _observed_semantics_clean(child_results)
                        wave_clean += int(child_semantic_clean)
                        prefix_preserved = preserves_verified_prefix(
                            panel_results, child_results)
                        accepted = (
                            prefix_preserved and acceptance_key(
                                child_results, child) > acceptance_key(
                                    active_results, active_attempt))
                        deterministic_row.update({
                            "behavior_key": list(child_key),
                            "differential": _differential_summary(
                                child_results),
                            "verified_prefix_preserved": prefix_preserved,
                            "accepted_for_next_round": accepted,
                        })
                        if prefix_preserved and _observed_semantics_clean(child_results):
                            shape = transition_policy.action_family(
                                "deterministic-exactness-search", variant.label) + ":" + _sha(
                                    "\n".join(transition_policy.residual_content(child.diff)))
                            retained = frontier.offer(candidate_frontier.Candidate(
                                variant.source, child, child_results, child_assembly, tag,
                                acceptance_key(child_results, child), shape))
                            deterministic_row["offered_to_frontier"] = retained
                            deterministic_row["frontier_parent_attempt_id"] = panel_attempt.receipt_id
                        if accepted:
                            wave_improved += 1
                            child_resynchronization, child_bundle = \
                                _resynchronize(
                                    normalized_target, child_assembly,
                                    cases=_diagnostic_cases(
                                        cases, child_results),
                                    call_arities=call_arities,
                                    return_registers=return_registers)
                            child_bundle = _operation_feedback(
                                child_results, variant.source, child_bundle)
                            active_source, active_attempt = variant.source, child
                            active_results, active_assembly = (
                                child_results, child_assembly)
                            active_resynchronization = child_resynchronization
                            active_divergence_bundle = child_bundle
                            if acceptance_key(
                                    child_results, child) > acceptance_key(
                                        best_results, best_attempt):
                                best_source, best_attempt = variant.source, child
                                best_results, best_tag = child_results, tag
                                best_resynchronization = child_resynchronization
                                best_divergence_bundle = child_bundle
                            artifact = output.with_name(
                                f"{output.stem}.round{round_index}."
                                f"statement-order{search_wave}-{variant_index}.c")
                            artifact.write_text(
                                variant.source, encoding="utf-8")
                            deterministic_row["source_path"] = str(artifact)
                            accepted_variant = True
                    probe = residual_sites.intervention(
                        panel_source, variant.source, panel_attempt.diff, child.diff,
                        variant.label, compiled=child.compiled,
                        semantic_clean=child_semantic_clean, exact=child.exact)
                    deterministic_row["source_intervention"] = probe
                    source_history.append({"source_intervention": probe})
                    source_mapping["compiler_interventions"].append(probe)
                    residual_sites.record(conn, child.receipt_id, probe)
                    deterministic_iterations.append(deterministic_row)
                    _atomic_json(output, receipt)
                    # Finish the bounded sibling panel before expanding its
                    # winner. First-improvement abandonment silently loses
                    # alternatives that the winning source cannot regenerate.
                    if active_attempt.exact:
                        break
                receipt.setdefault("compiler_output_diversity", []).append({
                    "round": round_index, "search_wave": search_wave,
                    "parent_attempt_id": panel_attempt.receipt_id,
                    "tested": wave_tested, "compiled": wave_compiled,
                    "semantic_clean": wave_clean,
                    "distinct_interpreter_inputs": len(wave_outputs),
                    "distinct_nonparent_inputs": len(wave_outputs - {parent_assembly_sha}),
                    "champion_improvements": wave_improved,
                    "all_compiled_outputs_equal_parent": bool(wave_outputs) and
                        wave_outputs == {parent_assembly_sha},
                })
                receipt["candidate_frontier"] = {
                    "width": 4, "max_expansions_per_round": exactness_expansions,
                    "expanded_sources": len(frontier.expanded),
                    "pending_attempt_ids": [c.attempt.receipt_id for c in frontier.pending],
                    "champion_attempt_id": best_attempt.receipt_id}

            if active_attempt.exact:
                termination_reason = "byte exact after deterministic exactness search"
                break

            if deterministic_only:
                termination_reason = "deterministic exactness search exhausted"
                break

            active_gradient = exactness_gradient.build(
                active_attempt.diff, active_source,
                history=prior_exactness_history + deterministic_iterations,
                rejected=rejected)
            controller_exactness_handoff = (
                _observed_semantics_clean(active_results) and
                active_gradient.residual_classification.startswith(
                    ("register-operand-only",
                     "register-or-local-order-only"))
            )
            source_fallback_feed = ""
            if _observed_semantics_clean(active_results):
                source_fallback_feed = residual_sites.render(
                    active_source, function, active_attempt.diff,
                    residual_sites.load(conn, active_source, active_attempt.diff),
                    direct=active_attempt.source_attribution)
                receipt.setdefault("source_targeted_fallbacks", []).append({
                    "round": round_index, "parent_attempt_id": active_attempt.receipt_id,
                    "source_sha256": _sha(active_source), "model": model,
                    "evidence": source_fallback_feed})
            diagnosis_prompt = (
                "CONTROLLER EXACTNESS HANDOFF (no model diagnosis call):\n" +
                active_gradient.render()
                if controller_exactness_handoff else
                build_diagnosis_prompt(
                    raw_target, active_assembly, active_source,
                    active_attempt, active_results, rejected,
                    active_divergence_bundle, project_context,
                    prior_exactness_history + deterministic_iterations)
            )
            if source_fallback_feed:
                diagnosis_prompt += "\n\n" + source_fallback_feed
            workspace.assert_uncontaminated(diagnosis_prompt, repo, function)
            diagnosis_seed = seed + (round_index - 1) * 2
            started = time.time()
            try:
                if controller_exactness_handoff:
                    diagnosis = ""
                    diagnosis_meta = {
                        "eval_count": 0,
                        "_cache_hit": True,
                        "done_reason": "controller-exactness-handoff",
                    }
                else:
                    diagnosis, diagnosis_meta = llm.generate(
                        endpoint, model, diagnosis_prompt, timeout=timeout,
                        think=think, num_thread=num_thread,
                        temperature=temperature,
                        num_predict=diagnosis_num_predict,
                        seed=diagnosis_seed, cache_dir=cache_dir,
                        cache_namespace=(
                            f"differential-repair-v{PROMPT_VERSION}-diagnosis"))
            except Exception as exc:
                diagnosis_wall_ms = int((time.time() - started) * 1000)
                diagnosis_id = attempt_receipts.record_model_proposal(
                    conn, run_id=run_id,
                    parent_attempt_id=active_attempt.receipt_id,
                    prompt=diagnosis_prompt, raw_response=str(exc),
                    status="generation-error", model=model,
                    kind="differential-debugger-diagnosis",
                    sampling={"seed": diagnosis_seed, "round": round_index,
                              "phase": "diagnosis"},
                    wall_ms=diagnosis_wall_ms)
                consecutive_stalls += 1
                iterations.append({
                    "round": round_index,
                    "diagnosis_seed": diagnosis_seed,
                    "diagnosis_proposal_id": diagnosis_id,
                    "status": "diagnosis-generation-error",
                    "error": str(exc),
                    "consecutive_stalls": consecutive_stalls,
                })
                _atomic_json(output, receipt)
                if consecutive_stalls >= max_stalls:
                    termination_reason = "stalled after diagnosis errors"
                    break
                continue

            diagnosis_wall_ms = int((time.time() - started) * 1000)
            diagnosis_tokens = int(diagnosis_meta.get("eval_count", 0) or 0)
            recorded_tokens += diagnosis_tokens
            if not diagnosis_meta.get("_cache_hit"):
                charged_tokens += diagnosis_tokens
            diagnosis_id = attempt_receipts.record_model_proposal(
                conn, run_id=run_id,
                parent_attempt_id=active_attempt.receipt_id,
                prompt=diagnosis_prompt, raw_response=diagnosis,
                status=("controller-handoff" if controller_exactness_handoff
                        else "diagnosis" if diagnosis_complete(diagnosis, diagnosis_meta)
                        else "incomplete-diagnosis"),
                model=("" if controller_exactness_handoff else model),
                kind=("deterministic-exactness-handoff"
                      if controller_exactness_handoff else
                      "differential-debugger-diagnosis"),
                hypothesis=diagnosis[:1000],
                sampling={
                    "seed": diagnosis_seed, "round": round_index,
                    "phase": ("controller-exactness-handoff"
                              if controller_exactness_handoff else
                              "diagnosis"),
                    "temperature": temperature,
                    "cache_hit": bool(diagnosis_meta.get("_cache_hit")),
                    "cache_key": diagnosis_meta.get("_cache_key"),
                    "done_reason": diagnosis_meta.get("done_reason"),
                }, wall_ms=diagnosis_wall_ms, token_cost=diagnosis_tokens)

            if not controller_exactness_handoff and not diagnosis_complete(diagnosis, diagnosis_meta):
                completion_prompt = diagnosis_completion_prompt(
                    active_source, diagnosis,
                    _prioritized_feedback(active_results, active_source, active_divergence_bundle) + "\n\n" +
                    mechanical_opcode_to_c_bridge(active_source, active_divergence_bundle) + "\n\n" +
                    active_gradient.render())
                workspace.assert_uncontaminated(completion_prompt, repo, function)
                completion_started = time.time()
                completion_error = None
                try:
                    completed, completion_meta = llm.generate(
                        endpoint, model, completion_prompt, timeout=timeout,
                        think="false", num_thread=num_thread, temperature=temperature,
                        num_predict=4096, seed=diagnosis_seed + 1000000,
                        cache_dir=cache_dir, cache_namespace="diagnosis-completion-v1")
                except Exception as exc:
                    completed, completion_meta = "", {"done_reason": "error"}
                    completion_error = f"{type(exc).__name__}: {exc}"
                completion_tokens = int(completion_meta.get("eval_count", 0) or 0)
                recorded_tokens += completion_tokens
                if not completion_meta.get("_cache_hit"):
                    charged_tokens += completion_tokens
                completion_ok = diagnosis_complete(completed, completion_meta)
                completion_id = attempt_receipts.record_model_proposal(
                    conn, run_id=run_id, parent_attempt_id=active_attempt.receipt_id,
                    prompt=completion_prompt, raw_response=completed or completion_error or "",
                    status="diagnosis" if completion_ok else "incomplete-diagnosis",
                    model=model, kind="differential-debugger-diagnosis-completion",
                    sampling={"original_diagnosis_proposal_id": diagnosis_id,
                              "done_reason": completion_meta.get("done_reason"),
                              "cache_hit": bool(completion_meta.get("_cache_hit"))},
                    wall_ms=int((time.time() - completion_started) * 1000),
                    token_cost=completion_tokens)
                receipt.setdefault("diagnosis_completions", []).append({
                    "round": round_index, "original_proposal_id": diagnosis_id,
                    "completion_proposal_id": completion_id, "complete": completion_ok,
                    "tokens": completion_tokens, "error": completion_error})
                if not completion_ok:
                    iterations.append({"round": round_index,
                                       "status": "incomplete-diagnosis",
                                       "diagnosis_proposal_id": diagnosis_id,
                                       "completion_proposal_id": completion_id,
                                       "patch_skipped": True})
                    consecutive_stalls += 1
                    _atomic_json(output, receipt)
                    if consecutive_stalls >= max_stalls:
                        termination_reason = "stalled on incomplete diagnosis; no speculative patch emitted"
                        break
                    continue
                diagnosis, diagnosis_id = completed, completion_id
                diagnosis_tokens += completion_tokens
                diagnosis_wall_ms += int((time.time() - completion_started) * 1000)

            patch_prompt = build_patch_prompt(
                active_source, diagnosis, active_results, rejected,
                active_attempt.diff, active_divergence_bundle,
                prior_exactness_history + deterministic_iterations)
            if source_fallback_feed:
                patch_prompt += "\n\n" + source_fallback_feed
            workspace.assert_uncontaminated(patch_prompt, repo, function)
            patch_seed = diagnosis_seed + 1
            started = time.time()
            try:
                text, patch_meta = llm.generate(
                    endpoint, model, patch_prompt, timeout=timeout, think="false",
                    num_thread=num_thread, temperature=temperature,
                    num_predict=patch_num_predict, seed=patch_seed,
                    cache_dir=cache_dir,
                    cache_namespace=(
                        f"differential-repair-v{PROMPT_VERSION}-patch"),
                    prefill=JSON_PREFILL)
            except Exception as exc:
                patch_wall_ms = int((time.time() - started) * 1000)
                proposal_id = attempt_receipts.record_model_proposal(
                    conn, run_id=run_id,
                    parent_attempt_id=active_attempt.receipt_id,
                    prompt=patch_prompt, raw_response=str(exc),
                    status="generation-error", model=model,
                    kind="differential-debugger-repair",
                    sampling={"seed": patch_seed, "round": round_index,
                              "phase": "patch",
                              "diagnosis_proposal_id": diagnosis_id},
                    wall_ms=patch_wall_ms)
                consecutive_stalls += 1
                iterations.append({
                    "round": round_index,
                    "diagnosis_seed": diagnosis_seed,
                    "diagnosis_proposal_id": diagnosis_id,
                    "diagnosis": diagnosis,
                    "diagnosis_tokens": diagnosis_tokens,
                    "patch_seed": patch_seed,
                    "proposal_id": proposal_id,
                    "status": "patch-generation-error",
                    "error": str(exc),
                    "consecutive_stalls": consecutive_stalls,
                })
                _atomic_json(output, receipt)
                if consecutive_stalls >= max_stalls:
                    termination_reason = "stalled after patch errors"
                    break
                continue

            patch_wall_ms = int((time.time() - started) * 1000)
            patch_tokens = int(patch_meta.get("eval_count", 0) or 0)
            recorded_tokens += patch_tokens
            if not patch_meta.get("_cache_hit"):
                charged_tokens += patch_tokens
            patch_c89_normalized = False
            try:
                proposal, child_source, edit_format = parse_and_apply_patch(
                    text, active_source)
                normalized_source = c89.to_c89(child_source)
                patch_c89_normalized = normalized_source != child_source
                child_source = normalized_source
                validate_observable_locks(
                    active_source, child_source, active_results)
                validate_exactness_shape_edit(
                    active_source, child_source, active_results,
                    active_attempt.diff)
                status, error = "valid", ""
            except ValueError as exc:
                proposal, child_source = None, ""
                edit_format = "invalid"
                status, error = "invalid", str(exc)
            proposal_id = attempt_receipts.record_model_proposal(
                conn, run_id=run_id,
                parent_attempt_id=active_attempt.receipt_id,
                prompt=patch_prompt, raw_response=text, status=status,
                model=model, kind="differential-debugger-repair",
                hypothesis=proposal.hypothesis if proposal else "",
                edits=[{"old": edit.old, "new": edit.new}
                       for edit in proposal.edits] if proposal else [],
                sampling={
                    "seed": patch_seed, "round": round_index,
                    "phase": "patch", "temperature": temperature,
                    "diagnosis_proposal_id": diagnosis_id,
                    "cache_hit": bool(patch_meta.get("_cache_hit")),
                    "cache_key": patch_meta.get("_cache_key"),
                    "done_reason": patch_meta.get("done_reason"),
                    "edit_format": edit_format,
                    "c89_normalized": patch_c89_normalized,
                }, wall_ms=patch_wall_ms, token_cost=patch_tokens)
            patch_attempts = [{
                "attempt": 0, "seed": patch_seed,
                "proposal_id": proposal_id, "status": status,
                "error": error, "tokens": patch_tokens,
                "wall_ms": patch_wall_ms,
                "prompt_sha256": _sha(patch_prompt),
                "response_sha256": _sha(text),
            }]
            total_patch_tokens = patch_tokens
            total_patch_wall_ms = patch_wall_ms
            for retry_index in range(1, patch_retries + 1):
                if status == "valid":
                    break
                retry_prompt = build_patch_retry_prompt(
                    active_source, text, error, diagnosis,
                    active_divergence_bundle or
                    _causal_feedback(active_results),
                    active_attempt.diff,
                    prior_exactness_history + deterministic_iterations,
                    rejected)
                if source_fallback_feed:
                    retry_prompt += "\n\n" + source_fallback_feed
                workspace.assert_uncontaminated(
                    retry_prompt, repo, function)
                retry_seed = seed + 100000 + round_index * 10 + retry_index
                retry_started = time.time()
                retry_c89_normalized = False
                try:
                    retry_text, retry_meta = llm.generate(
                        endpoint, model, retry_prompt, timeout=timeout,
                        think="false", num_thread=num_thread,
                        temperature=temperature,
                        num_predict=patch_num_predict, seed=retry_seed,
                        cache_dir=cache_dir,
                        cache_namespace=(
                            f"differential-repair-v{PROMPT_VERSION}-patch-retry"),
                        prefill=JSON_PREFILL)
                    retry_wall_ms = int(
                        (time.time() - retry_started) * 1000)
                    retry_tokens = int(retry_meta.get("eval_count", 0) or 0)
                    recorded_tokens += retry_tokens
                    if not retry_meta.get("_cache_hit"):
                        charged_tokens += retry_tokens
                    try:
                        (retry_proposal, retry_source,
                         retry_edit_format) = parse_and_apply_patch(
                            retry_text, active_source)
                        normalized_source = c89.to_c89(retry_source)
                        retry_c89_normalized = \
                            normalized_source != retry_source
                        retry_source = normalized_source
                        validate_observable_locks(
                            active_source, retry_source, active_results)
                        validate_exactness_shape_edit(
                            active_source, retry_source, active_results,
                            active_attempt.diff)
                        retry_status, retry_error = "valid", ""
                    except ValueError as exc:
                        retry_proposal, retry_source = None, ""
                        retry_edit_format = "invalid"
                        retry_status, retry_error = "invalid", str(exc)
                    retry_id = attempt_receipts.record_model_proposal(
                        conn, run_id=run_id,
                        parent_attempt_id=active_attempt.receipt_id,
                        prompt=retry_prompt, raw_response=retry_text,
                        status=retry_status, model=model,
                        kind="differential-debugger-repair-retry",
                        hypothesis=(retry_proposal.hypothesis
                                    if retry_proposal else ""),
                        edits=[{"old": edit.old, "new": edit.new}
                               for edit in retry_proposal.edits]
                        if retry_proposal else [],
                        sampling={
                            "seed": retry_seed, "round": round_index,
                            "phase": "patch-retry",
                            "retry": retry_index,
                            "diagnosis_proposal_id": diagnosis_id,
                            "previous_proposal_id": proposal_id,
                            "cache_hit": bool(retry_meta.get("_cache_hit")),
                            "cache_key": retry_meta.get("_cache_key"),
                            "done_reason": retry_meta.get("done_reason"),
                            "edit_format": retry_edit_format,
                            "c89_normalized": retry_c89_normalized,
                        }, wall_ms=retry_wall_ms, token_cost=retry_tokens)
                except Exception as exc:
                    retry_text, retry_meta = str(exc), {}
                    retry_wall_ms = int(
                        (time.time() - retry_started) * 1000)
                    retry_tokens = 0
                    retry_proposal, retry_source = None, ""
                    retry_edit_format = "generation-error"
                    retry_status, retry_error = "generation-error", str(exc)
                    retry_id = attempt_receipts.record_model_proposal(
                        conn, run_id=run_id,
                        parent_attempt_id=active_attempt.receipt_id,
                        prompt=retry_prompt, raw_response=str(exc),
                        status=retry_status, model=model,
                        kind="differential-debugger-repair-retry",
                        sampling={
                            "seed": retry_seed, "round": round_index,
                            "phase": "patch-retry", "retry": retry_index,
                            "diagnosis_proposal_id": diagnosis_id,
                            "previous_proposal_id": proposal_id,
                        }, wall_ms=retry_wall_ms)
                total_patch_tokens += retry_tokens
                total_patch_wall_ms += retry_wall_ms
                patch_attempts.append({
                    "attempt": retry_index, "seed": retry_seed,
                    "proposal_id": retry_id, "status": retry_status,
                    "error": retry_error, "tokens": retry_tokens,
                    "wall_ms": retry_wall_ms,
                    "prompt_sha256": _sha(retry_prompt),
                    "response_sha256": _sha(retry_text),
                })
                patch_prompt, text, patch_meta, patch_seed = \
                    retry_prompt, retry_text, retry_meta, retry_seed
                proposal_id, proposal, child_source = \
                    retry_id, retry_proposal, retry_source
                edit_format = retry_edit_format
                patch_c89_normalized = retry_c89_normalized
                status, error = retry_status, retry_error
            patch_tokens = total_patch_tokens
            patch_wall_ms = total_patch_wall_ms
            row = {
                "round": round_index,
                "diagnosis_seed": diagnosis_seed,
                "diagnosis_proposal_id": diagnosis_id,
                "diagnosis_prompt_sha256": _sha(diagnosis_prompt),
                "diagnosis_response_sha256": _sha(diagnosis),
                "diagnosis": diagnosis,
                "diagnosis_tokens": diagnosis_tokens,
                "diagnosis_wall_ms": diagnosis_wall_ms,
                "controller_exactness_handoff": controller_exactness_handoff,
                "patch_seed": patch_seed,
                "proposal_id": proposal_id, "status": status,
                "patch_prompt_sha256": _sha(patch_prompt),
                "patch_response_sha256": _sha(text),
                "patch_tokens": patch_tokens,
                "patch_wall_ms": patch_wall_ms, "error": error,
                "edit_format": edit_format,
                "c89_normalized": patch_c89_normalized,
                "patch_attempts": patch_attempts,
                "input_resynchronization": _resynchronization_summary(
                    active_resynchronization, active_divergence_bundle),
            }
            if proposal is not None:
                row.update({
                    "kind": proposal.kind,
                    "hypothesis": proposal.hypothesis,
                    "edits": [
                        {"old": edit.old, "new": edit.new}
                        for edit in proposal.edits
                    ],
                })
            if status != "valid":
                consecutive_stalls += 1
                row["consecutive_stalls"] = consecutive_stalls
                rejected.append(
                    f"round {round_index}: invalid proposal: {error}")
                iterations.append(row)
                _atomic_json(output, receipt)
                if consecutive_stalls >= max_stalls:
                    termination_reason = "stalled after invalid patches"
                    break
                continue

            # A semantic edit can get the field order right and state the
            # binary-backed offsets in comments while still omitting the
            # padding needed to make those comments true. Resolve that
            # contradiction mechanically before compiling. ``repad`` only
            # acts when a claimed offset is present in binary evidence.
            observed_offsets = _binary_observed_offsets(conn, function)
            repadded_source, repadded = structgen.repad(
                child_source, observed_offsets)
            if repadded:
                child_source = repadded_source
                row["deterministic_postprocess"] = {
                    "name": "binary-evidenced-struct-repad",
                    "observed_offsets": sorted(observed_offsets),
                    "source_sha256": _sha(child_source),
                }

            tag = f"{function}_differential_repair_{round_index}_{time.time_ns()}"
            combined_wall_ms = diagnosis_wall_ms + patch_wall_ms
            combined_tokens = diagnosis_tokens + patch_tokens
            child = workspace.score(
                ws, repo, tag, child_source, conn=conn, func=function,
                iteration=round_index,
                strategy="differential-debugger-causal-repair", model=model,
                prompt=patch_prompt, temperature=temperature,
                wall_ms=combined_wall_ms,
                run_id=run_id, token_cost=combined_tokens,
                parent_attempt_id=active_attempt.receipt_id,
                relation="differential-causal-repair",
                action=proposal.hypothesis,
                feedback=active_divergence_bundle,
                run_kind="differential-debugger-repair", run_config=config,
                extra={"diagnosis_seed": diagnosis_seed,
                       "patch_seed": patch_seed,
                       "diagnosis_proposal_id": diagnosis_id,
                       "proposal_id": proposal_id,
                       "diagnosis_cache_hit": bool(
                           diagnosis_meta.get("_cache_hit")),
                       "patch_cache_hit": bool(patch_meta.get("_cache_hit"))},
                raw_response=text, extract_status="structured-edit",
                done_reason=patch_meta.get("done_reason", ""))
            if child.receipt_id:
                attempt_receipts.link_model_proposal(
                    conn, proposal_id, child.receipt_id)
            source_artifact = output.with_name(
                f"{output.stem}.round{round_index}.c")
            source_artifact.write_text(child_source, encoding="utf-8")
            compiler_repairs: list[dict] = []
            for compiler_index in range(1, compiler_retries + 1):
                if child.compiled:
                    break
                compiler_prompt = build_compiler_fix_prompt(
                    child_source, child.compiler_stderr, diagnosis)
                workspace.assert_uncontaminated(
                    compiler_prompt, repo, function)
                compiler_seed = (
                    seed + 200000 + round_index * 10 + compiler_index)
                compiler_started = time.time()
                compiler_c89_normalized = False
                try:
                    compiler_text, compiler_meta = llm.generate(
                        endpoint, model, compiler_prompt, timeout=timeout,
                        think="false", num_thread=num_thread,
                        temperature=temperature,
                        num_predict=patch_num_predict, seed=compiler_seed,
                        cache_dir=cache_dir,
                        cache_namespace=(
                            f"differential-repair-v{PROMPT_VERSION}-compiler"),
                        prefill=COMPILER_JSON_PREFILL)
                    compiler_wall_ms = int(
                        (time.time() - compiler_started) * 1000)
                    compiler_tokens = int(
                        compiler_meta.get("eval_count", 0) or 0)
                    recorded_tokens += compiler_tokens
                    if not compiler_meta.get("_cache_hit"):
                        charged_tokens += compiler_tokens
                    try:
                        (compiler_proposal, compiler_source,
                         compiler_edit_format) = parse_and_apply_patch(
                            compiler_text, child_source)
                        normalized_source = c89.to_c89(compiler_source)
                        compiler_c89_normalized = \
                            normalized_source != compiler_source
                        compiler_source = normalized_source
                        validate_exactness_shape_edit(
                            active_source, compiler_source, active_results,
                            active_attempt.diff)
                        compiler_status, compiler_error = "valid", ""
                    except ValueError as exc:
                        compiler_proposal, compiler_source = None, ""
                        compiler_edit_format = "invalid"
                        compiler_status, compiler_error = "invalid", str(exc)
                except Exception as exc:
                    compiler_text, compiler_meta = str(exc), {}
                    compiler_wall_ms = int(
                        (time.time() - compiler_started) * 1000)
                    compiler_tokens = 0
                    compiler_proposal, compiler_source = None, ""
                    compiler_edit_format = "generation-error"
                    compiler_status, compiler_error = \
                        "generation-error", str(exc)
                compiler_id = attempt_receipts.record_model_proposal(
                    conn, run_id=run_id,
                    parent_attempt_id=child.receipt_id,
                    prompt=compiler_prompt, raw_response=compiler_text,
                    status=compiler_status, model=model,
                    kind="differential-debugger-compiler-fix",
                    hypothesis=(compiler_proposal.hypothesis
                                if compiler_proposal else ""),
                    edits=[{"old": edit.old, "new": edit.new}
                           for edit in compiler_proposal.edits]
                    if compiler_proposal else [],
                    sampling={
                        "seed": compiler_seed, "round": round_index,
                        "phase": "compiler-fix", "retry": compiler_index,
                        "diagnosis_proposal_id": diagnosis_id,
                        "semantic_proposal_id": proposal_id,
                        "cache_hit": bool(compiler_meta.get("_cache_hit")),
                        "cache_key": compiler_meta.get("_cache_key"),
                        "done_reason": compiler_meta.get("done_reason"),
                        "edit_format": compiler_edit_format,
                        "c89_normalized": compiler_c89_normalized,
                    }, wall_ms=compiler_wall_ms, token_cost=compiler_tokens)
                compiler_row = {
                    "attempt": compiler_index, "seed": compiler_seed,
                    "proposal_id": compiler_id,
                    "status": compiler_status, "error": compiler_error,
                    "edit_format": compiler_edit_format,
                    "tokens": compiler_tokens, "wall_ms": compiler_wall_ms,
                    "prompt_sha256": _sha(compiler_prompt),
                    "response_sha256": _sha(compiler_text),
                }
                if compiler_proposal is not None:
                    compiler_row["hypothesis"] = compiler_proposal.hypothesis
                    compiler_row["edits"] = [
                        {"old": edit.old, "new": edit.new}
                        for edit in compiler_proposal.edits
                    ]
                compiler_repairs.append(compiler_row)
                if compiler_status != "valid" or compiler_proposal is None:
                    continue
                compiler_tag = (
                    f"{function}_differential_repair_{round_index}_compilefix_"
                    f"{compiler_index}_{time.time_ns()}")
                fixed = workspace.score(
                    ws, repo, compiler_tag, compiler_source, conn=conn,
                    func=function, iteration=round_index,
                    strategy="differential-debugger-compiler-fix",
                    model=model, prompt=compiler_prompt,
                    temperature=temperature, wall_ms=compiler_wall_ms,
                    run_id=run_id, token_cost=compiler_tokens,
                    parent_attempt_id=child.receipt_id,
                    relation="differential-compiler-fix",
                    action=compiler_proposal.hypothesis,
                    feedback=child.compiler_stderr,
                    run_kind="differential-debugger-repair",
                    run_config=config,
                    extra={"seed": compiler_seed,
                           "proposal_id": compiler_id,
                           "semantic_proposal_id": proposal_id},
                    raw_response=compiler_text,
                    extract_status="structured-compiler-edit",
                    done_reason=compiler_meta.get("done_reason", ""))
                if fixed.receipt_id:
                    attempt_receipts.link_model_proposal(
                        conn, compiler_id, fixed.receipt_id)
                compiler_artifact = output.with_name(
                    f"{output.stem}.round{round_index}.compilefix"
                    f"{compiler_index}.c")
                compiler_artifact.write_text(
                    compiler_source, encoding="utf-8")
                compiler_row.update({
                    "attempt_result": _attempt_summary(fixed),
                    "source_path": str(compiler_artifact),
                    "source_sha256": _sha(compiler_source),
                    "artifact_tag": compiler_tag,
                })
                child, child_source, tag = fixed, compiler_source, compiler_tag
                source_artifact = compiler_artifact
            row["compiler_repairs"] = compiler_repairs
            row.update({
                "attempt": _attempt_summary(child),
                "source_path": str(source_artifact),
                "source_sha256": _sha(child_source),
                "artifact_tag": tag,
            })
            if not child.compiled:
                if source_fallback_feed:
                    probe = residual_sites.intervention(
                        active_source, child_source, active_attempt.diff, child.diff,
                        "oss-source-alternative", compiled=False,
                        semantic_clean=False, exact=False)
                    row["source_intervention"] = probe
                    residual_sites.record(conn, child.receipt_id, probe)
                consecutive_stalls += 1
                row["consecutive_stalls"] = consecutive_stalls
                rejected.append(
                    f"round {round_index}: did not compile: "
                    f"{child.compiler_stderr[:800]}")
                iterations.append(row)
                _atomic_json(output, receipt)
                if consecutive_stalls >= max_stalls:
                    termination_reason = "stalled after compiler errors"
                    break
                continue
            child_assembly = workspace.semantic_assembly(
                (ws / f"{tag}_object_dump_normalized.s").read_text(
                    errors="replace"), ws / f"{tag}.o")
            child_results = evaluate(
                normalized_target, child_assembly, cases=cases,
                call_arities=call_arities,
                return_registers=return_registers)
            child_resynchronization, child_divergence_bundle = _resynchronize(
                normalized_target, child_assembly,
                cases=_diagnostic_cases(cases, child_results),
                call_arities=call_arities,
                return_registers=return_registers)
            child_divergence_bundle = _operation_feedback(
                child_results, child_source, child_divergence_bundle)
            child_key = behavior_key(child_results, child)
            active_key = behavior_key(active_results, active_attempt)
            prefix_preserved = preserves_verified_prefix(
                active_results, child_results)
            accepted = prefix_preserved and acceptance_key(
                child_results, child) > acceptance_key(
                    active_results, active_attempt)
            row["behavior_key"] = list(child_key)
            row["differential"] = _differential_summary(child_results)
            row["result_resynchronization"] = _resynchronization_summary(
                child_resynchronization, child_divergence_bundle)
            row["verified_prefix_preserved"] = prefix_preserved
            row["accepted_for_next_round"] = accepted
            if source_fallback_feed:
                probe = residual_sites.intervention(
                    active_source, child_source, active_attempt.diff, child.diff,
                    "oss-source-alternative", compiled=True,
                    semantic_clean=_observed_semantics_clean(child_results),
                    exact=child.exact)
                row["source_intervention"] = probe
                residual_sites.record(conn, child.receipt_id, probe)
            if accepted and acceptance_key(
                    child_results, child) > acceptance_key(
                        best_results, best_attempt):
                best_source, best_attempt = child_source, child
                best_results, best_tag = child_results, tag
                best_resynchronization = child_resynchronization
                best_divergence_bundle = child_divergence_bundle
            if accepted:
                active_source, active_attempt = child_source, child
                active_results, active_assembly = child_results, child_assembly
                active_resynchronization = child_resynchronization
                active_divergence_bundle = child_divergence_bundle
                consecutive_stalls = 0
                edit_summary = " | ".join(
                    f"`{edit.old.strip()}` -> `{edit.new.strip()}`"
                    for edit in proposal.edits)
                divergences = list(dict.fromkeys(
                    result.first_divergence for result in child_results
                    if result.first_divergence))[:3]
                rejected.append(
                    f"VERIFIED ACCEPTED CONTRACT round {round_index}: retained "
                    f"edit {edit_summary[:1200]}. It advanced the semantic "
                    "behavior key while preserving the verified prefix. The "
                    "CURRENT C already contains it; do not reverse it unless a "
                    "replacement strictly improves the verifier. Current next "
                    f"divergence(s): {' | '.join(divergences)}.")
            else:
                consecutive_stalls += 1
                reason = ("regressed a verified per-case prefix" if not
                          prefix_preserved else "did not improve behavior key")
                edit_summary = " | ".join(
                    f"`{edit.old.strip()}` -> `{edit.new.strip()}`"
                    for edit in proposal.edits)
                divergences = list(dict.fromkeys(
                    row.first_divergence for row in child_results
                    if row.first_divergence))[:3]
                counterfactual = mechanical_opcode_to_c_bridge(
                    child_source, child_divergence_bundle)
                rejected.append(
                    f"round {round_index}: {reason} {list(child_key)}. "
                    f"Attempted edit: {edit_summary[:1000]}. "
                    "The compiled counterfactual was not retained; its first "
                    f"divergence(s): {' | '.join(divergences)}.\n"
                    "COUNTERFACTUAL MECHANICAL FACTS:\n" +
                    counterfactual[:3000])
            row["consecutive_stalls"] = consecutive_stalls
            iterations.append(row)
            _atomic_json(output, receipt)
            if best_attempt.exact:
                termination_reason = "byte exact"
                break
            if consecutive_stalls >= max_stalls:
                termination_reason = "stalled without verified progress"
                break
    finally:
        conn.close()

    best_source_out.parent.mkdir(parents=True, exist_ok=True)
    receipt["semantic_replay_cache"] = replay_cache.summary()
    best_source_out.write_text(best_source, encoding="utf-8")
    final_assembly = workspace.semantic_assembly(
        (ws / f"{best_tag}_object_dump_normalized.s").read_text(errors="replace"),
        ws / f"{best_tag}.o")
    final_semantics = semantic_certificate(
        normalized_target, final_assembly, best_source, cases, best_results,
        call_arities=call_arities, return_registers=return_registers)
    receipt.update({
        "status": "complete",
        "termination_reason": termination_reason,
        "recorded_tokens": recorded_tokens,
        "charged_tokens": charged_tokens,
        "result": {
            "best_attempt": _attempt_summary(best_attempt),
            "best_artifact_tag": best_tag,
            "best_source_path": str(best_source_out),
            "best_source_sha256": _sha(best_source),
            "behavior_key": list(behavior_key(best_results, best_attempt)),
            "differential": _differential_summary(best_results),
            "semantic_certificate": final_semantics,
            "target_coverage": final_semantics["target_coverage"],
            "candidate_coverage": final_semantics["candidate_coverage"],
            "resynchronization": _resynchronization_summary(
                best_resynchronization, best_divergence_bundle),
            "semantic_cases_passed": sum(
                row.status == "passed" for row in best_results),
            "all_semantic_cases_passed": all(
                row.status == "passed" for row in best_results),
            "all_target_observable_semantic_cases_passed": (
                _observed_semantics_clean(best_results)),
            "target_inconclusive_cases": sum(
                _target_inconclusive(row) for row in best_results),
            "exact": bool(best_attempt.exact),
        },
    })
    _atomic_json(output, receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    parser.add_argument("--db", type=Path,
                        default=Path.home() / "decomp/kb-sbk1.sqlite")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-parent-attempt-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--best-source-out", type=Path)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--endpoint")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--proposal-replay-budget", type=int, default=8)
    parser.add_argument("--exactness-expansions", type=int, default=4,
                        help="bounded source frontier expansions (1-16)")
    parser.add_argument("--proposal-cutoff", type=int)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--think", default="high")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.25)
    parser.add_argument("--diagnosis-num-predict", type=int, default=8000)
    parser.add_argument("--patch-num-predict", type=int, default=2000)
    parser.add_argument("--patch-retries", type=int, default=2)
    parser.add_argument("--compiler-retries", type=int, default=2)
    parser.add_argument("--max-stalls", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument(
        "--deterministic-only", action="store_true",
        help="run compiler-verified semantic/exactness generators without "
             "calling the model")
    parser.add_argument("--function", default=FUNCTION)
    parser.add_argument("--census", type=Path)
    parser.add_argument("--panel-receipt", type=Path,
                        help="replay selected cases from a coverage/stress worker receipt")
    parser.add_argument(
        "--stress-cases", type=int, default=0,
        help="replace the census panel with this many target-only balanced "
             "semantic stress cases")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    best = (args.best_source_out.expanduser().resolve()
            if args.best_source_out else output.with_suffix(".best.c"))
    repo = args.repo.expanduser().resolve()
    cases = None
    call_arities = None
    return_registers: tuple[str, ...] = ()
    mutable_registers: tuple[str, ...] = ()
    pointer_registers: tuple[str, ...] = ()
    if args.census:
        census = json.loads(
            args.census.expanduser().resolve().read_text(encoding="utf-8"))
        nodes = [row for row in census.get("dag", {}).get("nodes", [])
                 if row.get("function") == args.function]
        if len(nodes) != 1:
            raise ValueError(
                f"expected one census node for {args.function}, "
                f"found {len(nodes)}")
        node = nodes[0]
        cases = tuple(differential.TestCase(
            name=str(raw["name"]), seed=int(raw["seed"]),
            player_writes=tuple(tuple(item) for item in
                                raw.get("player_writes", [])),
            global_writes=tuple(tuple(item) for item in
                                raw.get("global_writes", [])),
            entry_registers=tuple(tuple(item) for item in
                                  raw.get("entry_registers", [])),
            call_returns=tuple(tuple(item) for item in
                               raw.get("call_returns", [])))
            for raw in node.get("target_exploration", {}).get(
                "selected_cases", []))
        call_arities = {
            str(name): int(arity) for name, arity in
            (node.get("abi", {}).get("provisional_call_arities") or {}).items()
        }
        function_abi = node.get("abi", {}).get("function", {})
        return_registers = tuple(str(name) for name in
            (function_abi.get("return_registers") or []))
        mutable_registers = tuple(str(name) for name in
            (function_abi.get("mutable_scalar_registers") or []))
        pointer_registers = tuple(
            str(name) for name in
            (function_abi.get("pointer_registers") or []) if name != "a0")
    if args.panel_receipt:
        if not args.census:
            raise ValueError("--panel-receipt requires --census for the ABI contract")
        from eval.semantic_stress_pilot import _cases_from_rows
        panel_receipt = json.loads(args.panel_receipt.read_text(encoding="utf-8"))
        if panel_receipt.get("config", {}).get("function") != args.function:
            raise ValueError("panel receipt belongs to a different function")
        expected_target = panel_receipt.get("coverage_worker", {}).get("target_assembly_sha256")
        if expected_target:
            ws = workspace.bootstrap(repo, args.function)
            target = workspace.semantic_assembly(
                (ws / "target_object_dump_normalized.s").read_text(errors="replace"), ws / "target.o")
            if _sha(target) != expected_target:
                raise ValueError("panel receipt was selected against a different target binary")
        cases = _cases_from_rows(panel_receipt.get("panel", {}).get("selected_cases", []))
        if not cases:
            raise ValueError("panel receipt contains no cases")
    if args.stress_cases:
        if not cases:
            raise ValueError("--stress-cases requires --census")
        ws = workspace.bootstrap(repo, args.function)
        target_assembly = workspace.semantic_assembly(
            (ws / "target_object_dump_normalized.s").read_text(
                errors="replace"), ws / "target.o")
        cases = differential.build_semantic_stress_panel(
            target_assembly, cases, target_name=args.function,
            call_arities=call_arities, return_registers=return_registers,
            mutable_entry_registers=mutable_registers,
            pointer_entry_registers=pointer_registers,
            max_cases=args.stress_cases).cases
    receipt = run(
        repo=repo, db=args.db.expanduser().resolve(),
        source_path=args.source.expanduser().resolve(),
        source_parent_attempt_id=args.source_parent_attempt_id,
        output=output, best_source_out=best, model=args.model,
        endpoint=args.endpoint or llm.host(), rounds=args.rounds,
        timeout=args.timeout, think=args.think, num_thread=args.num_thread,
        temperature=args.temperature,
        diagnosis_num_predict=args.diagnosis_num_predict,
        patch_num_predict=args.patch_num_predict,
        patch_retries=args.patch_retries,
        compiler_retries=args.compiler_retries,
        max_stalls=args.max_stalls,
        seed=args.seed,
        cache_dir=(args.cache_dir.expanduser().resolve()
                   if args.cache_dir else None),
        function=args.function, cases=cases, call_arities=call_arities,
        return_registers=return_registers,
        deterministic_only=args.deterministic_only,
        exactness_expansions=args.exactness_expansions,
        proposal_replay_budget=args.proposal_replay_budget,
        proposal_cutoff=args.proposal_cutoff)
    print(json.dumps({
        "status": receipt["status"],
        "iterations": len(receipt["iterations"]),
        "termination_reason": receipt["termination_reason"],
        "recorded_tokens": receipt["recorded_tokens"],
        "charged_tokens": receipt["charged_tokens"],
        "result": {
            "attempt_id": receipt["result"]["best_attempt"]["attempt_id"],
            "score": receipt["result"]["best_attempt"]["score"],
            "exact": receipt["result"]["exact"],
            "semantic_cases_passed": receipt["result"]["semantic_cases_passed"],
            "semantic_cases": receipt["result"]["differential"]["case_count"],
            "target_coverage": receipt["result"]["target_coverage"]["status"],
            "candidate_coverage": receipt["result"]["candidate_coverage"]["status"],
            "best_source": receipt["result"]["best_source_path"],
        },
        "output": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()

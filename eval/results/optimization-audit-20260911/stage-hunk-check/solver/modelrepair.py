"""Bounded local-model repair over compiler/oracle residuals.

The model is a proposal source, not the search controller.  It emits a small
structured edit against one parent; every child is compiled, scored, and
attached to that parent in the trajectory ledger.  A non-compiling child may
remain in the beam when it reveals the next compiler error, because IDO often
reports blockers one at a time. Invalid, refused and duplicate proposals are
logged separately from verifier attempts so they do not masquerade as compile
failures.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from kb import attempts as attempt_receipts
from solver import compilefix, llm, repair, residual, workspace


KINDS = {
    "control-flow", "type-width", "layout", "call-arguments", "expression",
    "declarations", "temporaries", "other",
}
MAX_EDITS = 4
MAX_EDIT_CHARS = 4000
MAX_GROWTH = 2000
FORBIDDEN_EDIT = re.compile(
    r"(?:\b(?:GLOBAL_ASM|INCLUDE_ASM|__asm__)\b|\basm\s*\(|"
    r"^\s*#\s*(?:include|pragma)\b|\.incbin\b)", re.I | re.M)
INCLUDE_LINE = re.compile(r"^[ \t]*#[ \t]*include\b[^\r\n]*", re.I | re.M)


def _include_lines(source: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in INCLUDE_LINE.findall(source))


def _introduces_escape(old: str, new: str) -> bool:
    # Includes remain deterministic harness inputs. A model may anchor an edit
    # on an existing single-line include, but may not add/change/remove one.
    old_headers, new_headers = _include_lines(old), _include_lines(new)
    if old_headers != new_headers or any(line.endswith("\\") for line in new_headers):
        return True
    return bool(FORBIDDEN_EDIT.search(INCLUDE_LINE.sub("", new)))

EDIT_SCHEMA = {
    "type": "object", "required": ["kind", "hypothesis", "edits"],
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": sorted(KINDS)},
        "hypothesis": {"type": "string", "minLength": 1, "maxLength": 400},
        "edits": {"type": "array", "minItems": 1, "maxItems": MAX_EDITS,
                  "items": {"type": "object", "required": ["new"],
                            "oneOf": [{"required": ["old"]}, {"required": ["slot"]}],
                            "additionalProperties": False,
                            "properties": {"old": {"type": "string", "minLength": 1},
                                           "slot": {"type": "string", "pattern": "^([a-f0-9]{12}:)?(L[0-9]+|DECLARATIONS)$"},
                                           "new": {"type": "string"}}}}}}

PROMPT = """\
You are correcting one C candidate so the configured IDO compiler emits the target
MIPS instructions byte-for-byte. Make ONE {scope} source-level hypothesis.

Return JSON only, with this exact shape:
{{
  "kind": "one of: {kinds}",
  "hypothesis": "short explanation of the source-shape error",
  "edits": [{edit_example}]
}}

Rules:
- 1 to {max_edits} edits. {edit_rule}
- Do not return the complete file as one edit and do not change unrelated code.
- No inline assembly, GLOBAL_ASM, INCLUDE_ASM, or held-out/reference source.
- Preserve C89 and the existing function signature unless the residual proves it wrong.
- If a compiler error is present, fix that blocker before optimizing assembly.
- Assembly, instruction diffs and headers are READ-ONLY evidence, not editable source.
- The diff uses `-` for target instructions and `+` for compiled candidate instructions.
- Never put an instruction such as `addiu sp,sp,-0x30` in `old` or `new`.
  Stack/register changes must be induced by a C edit, not by editing compiler output.
- A high similarity score is not proof. Fix the diagnosed source shape.

TARGET ASSEMBLY (READ-ONLY):
```
{asm}
```

CURRENT C (weighted progress score {score:.3f}; this is NOT percent bytes):
```c
{code}
```

EXACTNESS-FIRST RESIDUAL PACKET:
```json
{residual}
```

CURRENT COMPILER ERROR:
```
{compiler_error}
```

CURRENT INSTRUCTION DIFF (READ-ONLY compiler output, NOT CURRENT C):
```
{diff}
```
{diagnosis}{history}
"""


@dataclass(frozen=True)
class Edit:
    old: str
    new: str
    slot: str = ""


@dataclass(frozen=True)
class Proposal:
    kind: str
    hypothesis: str
    edits: tuple[Edit, ...]
    source_sha256: str = ""


@dataclass(frozen=True)
class GenerationRequest:
    """Provider-neutral request for one bounded repair proposal."""
    prompt: str
    model: str
    endpoint: str
    timeout: int
    think: str
    num_thread: int
    temperature: float
    num_predict: int
    seed: int | None
    cache_dir: str | Path | None
    cache_namespace: str
    prefill: str = ""
    response_schema: dict | None = None


class ProposalProvider(Protocol):
    """Any local or hosted model adapter can drive the same search kernel."""
    provider_id: str

    def generate(self, request: GenerationRequest) -> tuple[str, dict]: ...


class OllamaProvider:
    provider_id = "ollama"

    def generate(self, request: GenerationRequest) -> tuple[str, dict]:
        return llm.generate(
            request.endpoint, request.model, request.prompt,
            timeout=request.timeout, think=request.think,
            num_thread=request.num_thread, temperature=request.temperature,
            num_predict=request.num_predict, seed=request.seed,
            cache_dir=request.cache_dir,
            cache_namespace=request.cache_namespace,
            prefill=request.prefill, response_schema=request.response_schema)


@dataclass
class CandidateState:
    source: str
    attempt: workspace.Attempt
    object_path: Path | None = None
    labels: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    semantic: dict | None = None


@dataclass
class Result:
    function: str
    best_attempt: workspace.Attempt
    best_source: str
    best_object_path: Path | None = None
    run_id: str = ""
    exact: bool = False
    calls_attempted: int = 0
    transport_events: list[dict] = field(default_factory=list)
    generations: int = 0
    tokens: int = 0
    charged_tokens: int = 0
    compiling_children: int = 0
    incomplete_responses: int = 0
    invalid_proposals: int = 0
    log: list[str] = field(default_factory=list)
    frontier: list[CandidateState] = field(default_factory=list)
    best_byte: CandidateState | None = None
    best_semantic: CandidateState | None = None
    normalization_candidates: int = 0


def _objects(text: str):
    """Yield JSON objects embedded in an otherwise noisy model response."""
    decoder = json.JSONDecoder()
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.I)
    sources = fenced + [text]
    for source in sources:
        for match in re.finditer(r"\{", source):
            try:
                value, _end = decoder.raw_decode(source[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def parse_proposal(text: str, *, truncate_hypothesis: bool = False,
                   normalize_kind: bool = False,
                   default_hypothesis: bool = False, source: str | None = None,
                   type_transaction: bool = False) -> Proposal:
    """Parse and bound one structured edit; raise ValueError on ambiguity.

    ``truncate_hypothesis`` is for controllers that separately retain a full
    diagnosis.  It never relaxes the edit count, size, uniqueness, or source
    escape checks; it only prevents verbose explanatory prose from discarding
    an otherwise structurally valid patch.
    """
    value = next(_objects(text), None)
    if value is None:
        raise ValueError("no JSON object")
    kind = value.get("kind")
    hypothesis = value.get("hypothesis")
    raw_edits = value.get("edits")
    if kind not in KINDS:
        if not normalize_kind:
            raise ValueError("unknown repair kind")
        kind = "other"
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        if not default_hypothesis:
            raise ValueError("missing hypothesis")
        hypothesis = "model-proposed bounded repair"
    if len(hypothesis) > 400:
        if not truncate_hypothesis:
            raise ValueError("hypothesis too long")
        hypothesis = hypothesis[:397].rstrip() + "..."
    from solver import type_transaction as transaction
    max_edits = transaction.MAX_EDITS if type_transaction else MAX_EDITS
    if not isinstance(raw_edits, list) or not 1 <= len(raw_edits) <= max_edits:
        raise ValueError(f"edits must contain 1 to {max_edits} replacements")

    edits = []
    changed_chars = 0
    for raw in raw_edits:
        if not isinstance(raw, dict):
            raise ValueError("edit is not an object")
        if type_transaction and (set(raw) != {'slot', 'new'} or source is None):
            raise ValueError('type transaction requires source-bound slot/new edits only')
        old, new, slot = raw.get("old", ""), raw.get("new"), raw.get("slot", "")
        if old == '' and slot == '':
            raise ValueError('empty old has no insertion location: use DECLARATIONS for file-scope declarations, '
                             'or replace the function-opening line slot with that line plus new local declarations')
        if (not isinstance(old, str) or not isinstance(new, str) or not isinstance(slot, str)
                or bool(old) == bool(slot)):
            raise ValueError("edit strings are invalid")
        if source is not None:
            from solver import edit_slots
            new = edit_slots.normalize_newlines(new)
            if re.fullmatch(r'(?:L[0-9]+|DECLARATIONS)', slot):
                slot = _digest(source)[:12] + ':' + slot
        if slot and not re.fullmatch(r"[a-f0-9]{12}:(?:L[0-9]+|DECLARATIONS)", slot):
            raise ValueError("invalid source slot")
        if not slot and old == new:
            raise ValueError("edit is a no-op")
        if _introduces_escape(old, new):
            raise ValueError("edit introduces a forbidden source escape")
        changed_chars += len(old) + len(new)
        edits.append(Edit(old, new, slot))
    if changed_chars > (transaction.MAX_CHARS if type_transaction else MAX_EDIT_CHARS):
        raise ValueError("edit budget exceeded")
    return Proposal(kind, hypothesis.strip(), tuple(edits), _digest(source) if source is not None else '')


def _edit_span(source: str, old: str, *,
               relaxed_whitespace: bool) -> tuple[int, int, bool]:
    count = source.count(old)
    if count == 1:
        start = source.index(old)
        return start, start + len(old), False
    if count or not relaxed_whitespace:
        raise ValueError(f"old substring occurs {count} times")

    chunks = re.split(r"\s+", old.strip())
    pattern = r"\s+".join(re.escape(chunk) for chunk in chunks if chunk)
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        raise ValueError(
            f"old substring occurs 0 times; whitespace-relaxed form occurs "
            f"{len(matches)} times")
    match = matches[0]
    return match.start(), match.end(), True


def apply_proposal(source: str, proposal: Proposal, *,
                   relaxed_whitespace: bool = False, type_transaction: bool = False) -> str:
    """Apply unique replacements without permitting a whole-file rewrite.

    Exact matching remains the default. Agentic controllers may opt into a
    whitespace-relaxed fallback that still requires one token-identical span.
    """
    if proposal.source_sha256 and proposal.source_sha256 != _digest(source):
        raise ValueError('proposal source identity changed')
    from solver import type_transaction as transaction
    max_chars = transaction.MAX_CHARS if type_transaction else MAX_EDIT_CHARS
    max_growth = transaction.MAX_GROWTH if type_transaction else MAX_GROWTH
    if type_transaction and (not proposal.source_sha256 or not all(e.slot for e in proposal.edits)
                            or len(proposal.edits) > transaction.MAX_EDITS):
        raise ValueError('type transaction requires bounded source-bound slots')
    if any(edit.slot for edit in proposal.edits):
        from solver import edit_slots
        for edit in proposal.edits:
            if _introduces_escape(edit.old, edit.new):
                raise ValueError("edit introduces a forbidden source escape")
        out = edit_slots.apply(source, proposal.edits, max_chars=max_chars)
        if _include_lines(out) != _include_lines(source):
            raise ValueError("edit introduces a forbidden source escape: changed include context")
        if len(out) - len(source) > max_growth:
            raise ValueError("proposal grows the source too much")
        if _semantic_text(out) == _semantic_text(source):
            raise ValueError("edit changes comments or whitespace only")
        return out
    for edit in proposal.edits:
        if _introduces_escape(edit.old, edit.new):
            raise ValueError("edit introduces a forbidden source escape")
        start, end, _relaxed = _edit_span(
            source, edit.old, relaxed_whitespace=relaxed_whitespace)
        if source[start:end].strip() == source.strip():
            raise ValueError("whole-file replacement is not allowed")
    out = source
    for edit in proposal.edits:
        try:
            start, end, relaxed = _edit_span(
                out, edit.old, relaxed_whitespace=relaxed_whitespace)
        except ValueError as exc:
            raise ValueError("edits overlap or invalidate one another") from exc
        replacement = edit.new
        if relaxed:
            line_start = out.rfind("\n", 0, start) + 1
            indent = out[line_start:start]
            if not indent.strip():
                replacement = replacement.replace("\n", "\n" + indent)
        out = out[:start] + replacement + out[end:]
    if out == source:
        raise ValueError("proposal produced no source change")
    if _include_lines(out) != _include_lines(source):
        raise ValueError("edit introduces a forbidden source escape: changed include context")
    if len(out) - len(source) > MAX_GROWTH:
        raise ValueError("proposal grows the source too much")
    if _semantic_text(out) == _semantic_text(source):
        raise ValueError("edit changes comments or whitespace only")
    return out


def build_prompt(asm: str, source: str, attempt: workspace.Attempt, *,
                 packet: residual.ResidualPacket | None = None,
                 diagnosis: str = "", history: tuple[str, ...] = (),
                 rejected: list[str] | None = None, type_transaction: bool = False) -> str:
    history_lines = list(history[-6:]) + list((rejected or [])[-8:])
    history_block = ""
    if history_lines:
        history_block = ("\nPREVIOUS/REJECTED HYPOTHESES:\n- "
                         + "\n- ".join(history_lines) + "\n")
    diagnosis_block = f"\nDETERMINISTIC DIAGNOSIS:\n{diagnosis}\n" if diagnosis else ""
    if attempt.frontend and attempt.frontend.get("passed") is False:
        diagnosis_block += ("\nPROJECT C FRONTEND REJECTED THIS CANDIDATE. The frontend diagnostics "
            "in the residual are source-bound repair obligations, even when object exact=true. "
            "Fix the reported declarations/types/calls while preserving the target object. "
            "Do not suppress warnings, change headers, or weaken the checker.\n")
    if attempt.compiler_recipe:
        diagnosis_block += ("\nCURRENT COMPILER RECIPE (read-only build context):\n"
                            + json.dumps({key: attempt.compiler_recipe.get(key) for key in
                                ("settings", "source_origin", "authority")}, sort_keys=True) + "\n")
    packet = packet or residual.build(attempt, target_asm=asm)
    from solver import edit_slots, type_transaction as transaction
    return PROMPT.format(
        kinds=", ".join(sorted(KINDS)), max_edits=transaction.MAX_EDITS if type_transaction else MAX_EDITS, asm=asm,
        scope='coordinated type-reconstruction' if type_transaction else 'narrow',
        edit_example='{"slot":"L23","new":"replacement for this complete line"}' if type_transaction
            else '{"old":"exact unique substring from CURRENT C","new":"replacement"}',
        edit_rule='Use ONLY source slot/new pairs from the table below. No old-substring edits.' if type_transaction
            else 'Use a source slot ID from the table below, OR an old substring that occurs exactly once in CURRENT C; never supply both.',
        score=attempt.score, code=source, diff=(attempt.diff or "")[:12000],
        compiler_error=(attempt.compiler_stderr or "")[:6000],
        residual=packet.render(), diagnosis=diagnosis_block,
        history=history_block) + edit_slots.render(source)


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _semantic_text(source: str) -> str:
    """Remove comments and normalize code whitespace, preserving literals."""
    out: list[str] = []
    index = 0
    pending_space = False
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if char.isspace():
            pending_space = True
            index += 1
            continue
        if char == "/" and following in ("/", "*"):
            pending_space = True
            if following == "/":
                newline = source.find("\n", index + 2)
                index = len(source) if newline < 0 else newline + 1
            else:
                end = source.find("*/", index + 2)
                index = len(source) if end < 0 else end + 2
            continue
        if pending_space and out:
            out.append(" ")
        pending_space = False
        if char not in ('"', "'"):
            out.append(char)
            index += 1
            continue
        quote = char
        out.append(char)
        index += 1
        while index < len(source):
            char = source[index]
            out.append(char)
            index += 1
            if char == "\\" and index < len(source):
                out.append(source[index])
                index += 1
            elif char == quote:
                break
    return "".join(out).strip()


def _frontier(states: list[CandidateState], width: int) -> list[CandidateState]:
    """Prefer compiling states; otherwise retain distinct error signatures."""
    compiling = [state for state in states if state.attempt.compiled]
    if compiling:
        completed = [state for state in compiling if workspace.repair_complete(state.attempt)]
        if completed:
            return completed[:width]
        if any(state.semantic and state.semantic.get('semantic_key') for state in compiling):
            identities = {state.semantic['panel_sha256'] for state in compiling
                          if state.semantic and state.semantic.get('semantic_key')}
            if len(identities) != 1:
                raise ValueError('cannot rank semantic results from different test panels')
            ordered = sorted(compiling,key=state_quality,reverse=True)
            unique = { _digest(state.source):state for state in reversed(ordered) }
            return sorted(unique.values(),key=state_quality,reverse=True)[:width]
        return repair._frontier(compiling, width)

    # A later edit with the same first error may have removed another blocker
    # that IDO had not reached yet, so depth is useful weak evidence here.
    ordered = sorted(
        states,
        key=lambda state: (*compile_error_rank(state.attempt), -len(state.labels), *context_rank(state.source),
                           compilefix.signature(state.attempt.compiler_stderr),
                           len(state.attempt.compiler_stderr),
                           _digest(state.source)))
    chosen: list[CandidateState] = []
    seen_errors: set[str] = set()
    for state in ordered:
        signature = compilefix.signature(state.attempt.compiler_stderr)
        if signature not in seen_errors:
            chosen.append(state)
            seen_errors.add(signature)
            if len(chosen) == width:
                return chosen
    for state in ordered:
        if state not in chosen:
            chosen.append(state)
            if len(chosen) == width:
                break
    return chosen


def context_rank(source: str) -> tuple[int, int]:
    """Search order only, not correctness: fewer m2c unknowns, richer headers."""
    unknowns = len(re.findall(r"(?:extern\s+|[,(]\s*)\?\s|(?m:^\s*)\?\s", source))
    return unknowns, -len(_include_lines(source))


def compile_error_rank(attempt: workspace.Attempt) -> tuple[int, int, int, int]:
    diagnostic = (attempt.frontend or {}).get('diagnostics', '')
    errors = [line for line in diagnostic.splitlines() if re.search(r'\b(?:fatal )?error:', line)]
    header_errors = sum(bool(re.match(r'(?!candidate\.c:).+\.h:\d+:\d+:', line)) for line in errors)
    # A helper-policy failure can mask all IDO diagnostics. Prefer an already
    # lowered equivalent draft on ties, rather than making the model lower it.
    helper_blocked = int('The C file contains a do-while loop' in attempt.compiler_stderr)
    absent_text = int('Compiled object has no text symbols' in attempt.compiler_stderr)
    return absent_text, header_errors, len(errors), helper_blocked


def compile_feedback(attempt: workspace.Attempt) -> str:
    errors = [line for line in (attempt.frontend or {}).get('diagnostics', '').splitlines()
              if re.search(r'\b(?:fatal )?error:', line)]
    return compilefix.signature(attempt.compiler_stderr) + (
        '; frontend: ' + ' | '.join(errors[:8])[:1800] if errors else '')


def _quality(attempt: workspace.Attempt) -> tuple:
    return (workspace.repair_complete(attempt), attempt.exact,
            attempt.compiled, bool(attempt.frontend and attempt.frontend.get("passed"))
            and 'Compiled object has no text symbols' not in attempt.compiler_stderr, attempt.score,
            *(-v for v in compile_error_rank(attempt)))


def state_quality(state):
    semantic = state.semantic or {}
    return (*_quality(state.attempt)[:4],
            bool(semantic.get('semantic_key')),
            tuple(semantic.get('semantic_key',())), *_quality(state.attempt)[4:])


def semantic_prompt(repo, name, state, asm, abi, rejected, type_transaction=False):
    """Put the failed behavioral gate first, not behind a byte-polish task."""
    from solver import compile_obligations, edit_slots
    report = state.semantic
    failures = report.get('feedback',[])
    packet = {key:report.get(key) for key in ('counts','total','debt','callee_source_contracts','operation_gradient','callee_environment')}
    packet['call_contracts'] = report.get('call_contracts', {})
    packet['source_object_obligations'] = report.get('source_object_obligations', [])
    obligations = report.get('opaque_stack_obligations', [])
    packet['indirect_call_obligations'] = report.get('indirect_call_obligations', [])[:4]
    packet['unknown_direct_argument_evidence'] = report.get('unknown_direct_argument_evidence', [])[:4]
    packet['opaque_stack_obligations'] = obligations[:8]
    packet['omitted_stack_obligations'] = max(0, len(obligations)-8)
    packet['primary_counterexample'] = failures[0] if failures else None
    packet['other_failure_classes'] = [{k:f.get(k) for k in ('input','reasons','target_return','candidate_return')}
                                       for f in failures[1:3]]
    return ('SEMANTIC REPAIR OBJECTIVE: fix an observed behavioral disagreement, then pursue byte exactness.\n'
        'The source already compiles. Explain the failing output value/call/memory dependency, '
        'not an unrelated branch or register-allocation difference. Preserve input dependence and public ABI. '
        'A signed branch after an unsigned load can be redundant; its presence is not proof that the public input is signed.\n'
        'Opaque stack-pointee obligations are testing limitations, not proven source errors. '
        'Do not rearrange locals merely to equalize stack addresses or treat synthetic opaque-call results as real callee behavior. '
        'Unknown call arities are diagnostic assumptions, not ABI facts.\n'
        'Return JSON only: {"kind":"expression", "hypothesis":"one causal hypothesis", '
        '"edits":[{"old":"exact unique source span from CURRENT C","new":"replacement source span"}]}. '
        'Use 1..'+str(64 if type_transaction else MAX_EDITS)+' source edits; no assembly, header, preprocessor, or whole-file edits.\n'
        'Edit the expression that produces the wrong value, including a condition operand when the executed branch differs. '
        'Keep unrelated control statements and braces intact; do not replace an enclosing block to change one expression. '
        'A line slot replaces ONE physical line; it does not replace the following block. '
        + ('In coordinated type mode use slot/new edits instead of old/new.\n' if type_transaction else '') +
        'PUBLIC ABI (read-only): '+json.dumps({k:v for k,v in abi.items() if k!='known_header_types'})+
        '\nFAILING OBSERVABLES AND EXECUTED VALUE TREES:\n'+json.dumps(packet)+
        '\nCURRENT C:\n'+state.source+(edit_slots.render(state.source) if type_transaction else '')+
        '\nACTUAL INCLUDED TYPE DEFINITIONS (header-assisted, read-only):\n'+json.dumps(
            compile_obligations.header_types(repo,state.source,name,max_chars=8000))+
        '\nTARGET INSTRUCTIONS (read-only; byte polish is secondary while behavior fails):\n'+asm+
        '\nRECENT VERIFIED REJECTIONS/OUTCOMES:\n'+'\n'.join(rejected[-4:])[-5000:])


def search(repo: Path, name: str, source: str, ws: Path, *,
           model: str, endpoint: str, conn=None,
           base_attempt: workspace.Attempt | None = None,
           base_object_path: Path | None = None,
           parent_attempt_id: int | None = None, diagnosis: str = "",
           draws: int = 4, max_depth: int = 2, beam_width: int = 4,
           max_calls: int = 12,
           timeout: int = 420, think: str = "low", num_thread: int = 12,
           temperature: float = 0.4, num_predict: int = 1800,
           seed: int | None = None, run_id: str = "",
           call_seeds: tuple[int, ...] | None = None,
           cache_dir: str | Path | None = None,
           cache_namespace: str = "", exhaust_budget: bool = False,
           provider: ProposalProvider | None = None,
           verbose: bool = False,
           initial_states: tuple[CandidateState, ...] = (),
           strategy_brief: str = "", structured_output: bool = False,
           retry_invalid: bool = False, include_header_context: bool = False,
           compile_only: bool = False, type_transaction: bool = False,
           resilient: bool = False, semantic_evaluator=None) -> Result:
    """Search a small model-proposed edit tree and retain a diverse frontier."""
    provider = provider or OllamaProvider()
    from solver import type_transaction as transaction, repair_context, type_plan
    response_schema = transaction.schema(EDIT_SCHEMA) if type_transaction else EDIT_SCHEMA
    abi = transaction.contract(repo, source, name) if type_transaction or resilient else None
    run_id = run_id or f"modelrepair-{time.time_ns()}-{name}"
    config = {
        "draws": draws, "max_depth": max_depth, "beam_width": beam_width,
        "max_calls": max_calls,
        "timeout": timeout, "think": think, "num_thread": num_thread,
        "temperature": temperature, "num_predict": num_predict, "seed": seed,
        "call_seeds": list(call_seeds or ()),
        "cache_namespace": cache_namespace,
        "exhaust_budget": exhaust_budget,
        "provider": provider.provider_id,
        "strategy_brief": strategy_brief, "structured_output": structured_output,
        "retry_invalid": retry_invalid, "include_header_context": include_header_context,
        "compile_only": compile_only,
        "type_transaction": type_transaction,
        "resilient": resilient,
        "normalization_round_limit": 8,
        "semantic_stride_candidate_limit": 4,
        "type_transaction_policy": {'max_edits':transaction.MAX_EDITS, 'max_chars':transaction.MAX_CHARS,
            'max_growth':transaction.MAX_GROWTH, 'lookahead':transaction.LOOKAHEAD, 'public_abi':abi} if type_transaction else None,
        "completion_handoff": "one low-effort schema-constrained emission retry, inside provider-call budget",
    }
    if conn is not None:
        attempt_receipts.start_run(
            conn, run_id, kind="model-repair", model=model, config=config)

    if base_attempt is None and conn is not None:
        workspace.configure_compiler(ws, repo, conn, name)
    base_tag = f"{name}_modelrepair_base"
    base = base_attempt or workspace.score(ws, repo, base_tag, source)
    if base_attempt is None and base.compiled:
        base_object_path = ws / f"{base_tag}.o"
    if parent_attempt_id is not None:
        base.receipt_id = parent_attempt_id
    elif conn is not None and base.receipt_id is None:
        workspace.record_attempt(
            conn, name, source, base, strategy="modelrepair-baseline",
            run_id=run_id, run_kind="model-repair", run_config=config)

    result = Result(
        name, base, source, best_object_path=base_object_path,
        run_id=run_id, exact=workspace.repair_complete(base))
    def evaluate(state):
        if semantic_evaluator and state.attempt.compiled:
            try:
                state.semantic = semantic_evaluator(state)
            except (OSError, ValueError) as exc:
                state.semantic = {'status':'unavailable','authoritative':False,
                                  'source_sha256':_digest(state.source),'reason':str(exc)}
                result.log.append('semantic evaluator unavailable: '+str(exc))
        if result.best_byte is None or _quality(state.attempt) > _quality(result.best_byte.attempt):
            result.best_byte = state
        if state.semantic and state.semantic.get('semantic_key'):
            if result.best_semantic is None or state_quality(state) > state_quality(result.best_semantic):
                result.best_semantic = state
        return state

    normalization_seen = {source, *(s.source for s in initial_states)}

    def normalize(state, tag):
        if not resilient or workspace.repair_complete(state.attempt): return []
        if 'Compiled object has no text symbols' in state.attempt.compiler_stderr:
            result.log.append('body-dependent normalization skipped: object has no text symbols; needs a function draft')
            return []
        from solver import stack_buffers, compile_obligations
        variants, stack_report = stack_buffers.candidates(state.source, workspace.target_asm(ws,name), name)
        subfields, subfield_report = stack_buffers.byte_subfields(state.source,workspace.target_asm(ws,name),name,
            (state.attempt.frontend or {}).get('diagnostics',''))
        variants += subfields
        header_report = None
        prototype_report = None
        frontend_diagnostics = (state.attempt.frontend or {}).get('diagnostics', '')
        from solver import frontend_repair
        from solver import address_units
        pointer_report = address_units.parameter_call_views(state.source, name,
            workspace.target_asm(ws, name), o32=frontend_repair.big_endian_o32(ws/'target.o'))
        if pointer_report['changes']:
            variants.append(('parameter-call-byte-units', pointer_report['source']))
        if pointer_report['examined']:
            result.log.append('parameter-call-byte-units: '+str(len(pointer_report['changes']))+
                ' changes, '+str(len(pointer_report['declines']))+' declines')
        format_buffer_report=None
        if 'M2C_UNK' in state.source and frontend_repair.big_endian_o32(ws/'target.o'):
            from solver import format_buffer_repair
            format_buffer_report=format_buffer_repair.propose(state.source,name,workspace.target_asm(ws,name))
            if format_buffer_report['changes']:
                variants.append(('format-buffer-endpoints',format_buffer_report['source']))
        literal_call_report=None
        if re.search(r'(?m)^\s*\?\s+\w+\s*\(',state.source) and frontend_repair.big_endian_o32(ws/'target.o'):
            from solver import literal_call_repair
            literal_call_report=literal_call_repair.propose(state.source,name,workspace.target_asm(ws,name))
            if literal_call_report['changes']:
                variants.append(('literal-word-call-prototype',literal_call_report['source']))
        from solver import byte_array_decay
        array_decay_report=byte_array_decay.propose(state.source,name,workspace.target_asm(ws,name),frontend_diagnostics)
        if array_decay_report['changes']:
            variants.append(('binary-byte-array-decay',array_decay_report['source']))
        stack_result_report = None
        if re.search(r"undeclared identifier 'sp[0-9A-Fa-f]+'",frontend_diagnostics) and frontend_repair.big_endian_o32(ws/'target.o'):
            from solver import stack_result_evidence, stack_result_repair
            try:
                callees=stack_result_evidence.load_callees(repo,state.source,name,frontend_diagnostics)
                stack_result_report=stack_result_repair.propose(state.source,name,
                    workspace.target_asm(ws,name),frontend_diagnostics,callees)
                if stack_result_report['changes']:
                    variants.append(('stack-result-wide-abi',stack_result_report['source']))
            except (OSError,ValueError) as exc:
                result.log.append('stack-result reconstruction unavailable: '+str(exc))
        from solver import void_field_repair
        void_field_report = void_field_repair.propose(state.source,name,
            workspace.target_asm(ws,name),frontend_diagnostics)
        if void_field_report['changes']:
            variants.append(('void-field-byte-view',void_field_report['source']))
        copy_report = None
        if ('implicit declaration of function' in frontend_diagnostics
                and 'M2C_MEMCPY_ALIGNED' in frontend_diagnostics
                and frontend_repair.big_endian_o32(ws/'target.o')):
            from solver import m2c_copy
            copy_report = m2c_copy.propose(state.source, name)
            if copy_report['changes']:
                variants.append(('m2c-aligned-copy', copy_report['source']))
        frontend_report = frontend_repair.propose(repo, state.source, name, frontend_diagnostics,
            big_endian_o32=frontend_repair.big_endian_o32(ws/'target.o'),
            target_assembly=workspace.target_asm(ws,name))
        from solver import local_record_repair
        from solver import negative_field_repair
        negative_report = negative_field_repair.propose(state.source, name,
            workspace.target_asm(ws,name), frontend_diagnostics)
        if negative_report['changes']:
            variants.append(('negative-typed-fields', negative_report['source']))
        record_report = None
        if frontend_repair.big_endian_o32(ws/'target.o'):
            record_report = local_record_repair.propose(state.source, workspace.target_asm(ws,name))
            if record_report['changes']:
                variants.append(('local-record-layout', record_report['source']))
        if frontend_report['changes']:
            variants.append(('frontend-representation', frontend_report['source']))
        if re.search(r"implicit declaration of function ['\"]\w+['\"]|unknown type name '\w+'", frontend_diagnostics):
            from solver import compile_recovery
            projected, prototype_report = compile_recovery.scalar_header_prototypes(
                repo, state.source, frontend_diagnostics)
            if projected != state.source:
                variants.append(('frontend-scalar-prototypes', projected))
            target = (state.attempt.compiler_recipe or {}).get('target', '')
            if target:
                try:
                    candidate, header_report = compile_recovery.header_variant(
                        repo, name, workspace.target_asm(ws,name), state.source, target)
                    if candidate != state.source:
                        variants.append(('frontend-header-context', candidate))
                except (OSError, ValueError) as exc:
                    result.log.append('frontend header recovery unavailable: '+str(exc))
        # Repaired-to-compilable children also need residual-guided search.
        # This is a hypothesis generator, not a semantics-preserving cleanup.
        stride_report = None
        if (not compile_only and state.attempt.compiled
                and (state.attempt.frontend or {}).get('passed') is True
                and (state.semantic or {}).get('status') == 'observed_failure'):
            from solver import rewrites
            proposals = rewrites.byte_pointer_step_rewrites(state.source, state.attempt.diff)
            limit = config['semantic_stride_candidate_limit']
            stride_report = {'source_sha256': _digest(state.source),
                'diff_sha256': _digest(state.attempt.diff),
                'hypotheses': [p.label for p in proposals[:limit]],
                'omitted': max(0, len(proposals)-limit),
                'authority': 'target stride correspondence hypothesis; compile and differential retest required'}
            variants += [('semantic-byte-stride-'+str(i), p.apply(state.source))
                         for i,p in enumerate(proposals[:limit])]
        from solver import pointer_spill_cleanup
        spill_report=pointer_spill_cleanup.propose(state.source,name)
        if spill_report['changes']:
            variants.append(('dead-pointer-copies',spill_report['source']))
        from solver import m2c_byte_view, project_headers
        cursor_report = m2c_byte_view.unknown_local_cursors(state.source, name,
            header_declarations=project_headers._included_declarations(repo,state.source)
            if 'M2C_UNK' in state.source else {})
        if cursor_report['changes']:
            variants.append(('unknown-byte-cursors', cursor_report['source']))
        byte_code, byte_report = compile_obligations.byte_pointer_variant(state.source,name,workspace.target_asm(ws,name))
        if byte_code != state.source:
            variants.append(('call-bound-byte-pointer-view',byte_code))
        from solver import address_units, m2c_adapter
        address_global_report = None
        if re.search(r'(?m)^\s*extern\s+(?:M2C_UNK|\?)\s+\w+\s*;',state.source):
            from solver import project_headers as address_headers, unknowns as address_unknowns
            address_symbols, identity_report = address_unknowns.address_symbol_evidence(repo,
                re.findall(r'(?m)^\s*extern\s+(?:M2C_UNK|\?)\s+(\w+)\s*;', state.source))
            address_global_report = address_units.address_only_globals(state.source,name,
                workspace.target_asm(ws,name),address_symbols,address_headers._included_declarations(repo,state.source))
            address_global_report['identity_evidence'] = identity_report
            if address_global_report['changes']:
                variants.append(('stored-symbol-addresses',address_global_report['source']))
        address_report = address_units.propose(state.source,name,workspace.target_asm(ws,name),
            absolute_symbols=m2c_adapter.absolute_symbols(repo))
        if address_report['changes']:
            variants.append(('binary-address-units',address_report['source']))
        from solver import pointer_stride_repair
        try:
            stride_target=json.loads((ws/'.compiler-target.json').read_text())['target']
            stride_report=pointer_stride_repair.recover(repo,ws,state.source,name,
                workspace.target_asm(ws,name),stride_target)
            if stride_report['changes']:
                variants.append(('measured-pointer-byte-stride',stride_report['source']))
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
            pass
        from solver import indexed_address_repair
        indexed_report = None
        try:
            target = json.loads((ws/'.compiler-target.json').read_text())['target']
            indexed_report = indexed_address_repair.recover(repo,ws,state.source,name,
                workspace.target_asm(ws,name),target)
            if indexed_report['changes']:
                variants.append(('measured-indexed-address',indexed_report['source']))
        except (OSError,ValueError,KeyError) as exc:
            result.log.append('indexed-address context unavailable: '+str(exc))
        partial_report = None
        diagnostics=(state.attempt.frontend or {}).get('diagnostics','')
        aggregate_report = None
        if re.search(r"assigning to '\w+'[^\n]* from incompatible type '\w+'|operand of type '\w+'[^\n]* where arithmetic or pointer type is required|variable has incomplete type 'struct \w+'",diagnostics):
            from solver import aggregate_scalar_repair, type_constraints
            try:
                target=json.loads((ws/'.compiler-target.json').read_text())['target']
                measured=type_constraints.measure(repo,ws,state.source,name,target)
                aggregate_report=aggregate_scalar_repair.propose(state.source,name,diagnostics,measured,
                    target_assembly=workspace.target_asm(ws,name))
                if aggregate_report['changes']:
                    variants.append(('measured-first-scalar-member',aggregate_report['source']))
            except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
                result.log.append('aggregate scalar recovery unavailable: '+str(exc))
        if re.search(r"no member named 'unk[0-9a-fA-F]+'",diagnostics):
            from solver import partial_word_fields, type_constraints
            try:
                target=json.loads((ws/'.compiler-target.json').read_text())['target']
                measured=type_constraints.measure(repo,ws,state.source,name,target)
                partial_report=partial_word_fields.propose(state.source,name,
                    workspace.target_asm(ws,name),measured,diagnostics)
                if partial_report['changes']:
                    variants.append(('measured-partial-word-fields',partial_report['source']))
            except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
                result.log.append('partial-word context unavailable: '+str(exc))
        object_report = None
        from solver import source_object_bounds
        if any(o.get('kind') == 'scalar-byte-view-extent-conflict'
                for o in source_object_bounds.obligations(state.source, name)):
            from solver import stack_object_repair, type_constraints
            try:
                target = json.loads((ws/'.compiler-target.json').read_text())['target']
                measured = type_constraints.measure(repo,ws,state.source,name,target)
                object_report = stack_object_repair.propose(state.source,name,
                    workspace.target_asm(ws,name),measured)
                if object_report['changes']:
                    variants.append(('measured-stack-object',object_report['source']))
                else:
                    result.log.append('stack-object reconstruction declined: '+str(object_report['declines']))
            except (OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
                result.log.append('stack-object reconstruction unavailable: '+str(exc))
        wide_report = None
        semantic_panel = getattr(semantic_evaluator,'panel',semantic_evaluator)
        environment = getattr(semantic_panel,'callee_environment',None)
        if environment is not None and any(c.get('status')=='result-width-conflict'
                for c in (state.semantic or {}).get('callee_source_contracts',[])):
            from solver import wide_return_repair
            try:
                elf = (ws/'target.o').read_bytes()[:6]
                byteorder = 'big' if elf[:4]==b'\x7fELF' and elf[5:6]==b'\x02' else 'unknown'
                wide_report = wide_return_repair.propose(state.source,name,environment,byteorder=byteorder)
                if wide_report['changes']:
                    variants.append(('binary-bound-wide-return',wide_report['source']))
                else:
                    result.log.append('wide-return reconstruction declined: '+str(wide_report['declines']))
            except (OSError,ValueError) as exc:
                result.log.append('wide-return reconstruction unavailable: '+str(exc))
        if not state.attempt.compiled:
            variants += repair_context.normalize(state.source,state.attempt.compiler_stderr,name)
        normalized = []
        for label,code in variants:
            if code == state.source or code in normalization_seen:
                continue
            normalization_seen.add(code)
            new_tag = tag+'_'+label
            att = workspace.score(ws,repo,new_tag,code,conn=conn,func=name,
                strategy='modelrepair-normalize:'+label, model='zero-model',run_id=run_id,
                parent_attempt_id=state.attempt.receipt_id,relation='compiler-normalization',
                extra=({'parameter_call_byte_units':pointer_report} if label=='parameter-call-byte-units' else
                       {'stack_buffer_hypotheses':stack_report} if label.startswith('stack-buffer-') else
                       {'header_context_hypotheses':header_report} if label=='frontend-header-context' else
                       {'header_prototype_hypotheses':prototype_report} if label=='frontend-scalar-prototypes' else
                       {'frontend_representation_hypotheses':frontend_report} if label=='frontend-representation' else
                       {'format_buffer_hypotheses':format_buffer_report} if label=='format-buffer-endpoints' else
                       {'literal_call_hypotheses':literal_call_report} if label=='literal-word-call-prototype' else
                       {'array_decay_hypotheses':array_decay_report} if label=='binary-byte-array-decay' else
                       {'stack_result_hypotheses':stack_result_report} if label=='stack-result-wide-abi' else
                       {'void_field_hypotheses':void_field_report} if label=='void-field-byte-view' else
                       {'m2c_copy_hypotheses':copy_report} if label=='m2c-aligned-copy' else
                       {'local_record_hypotheses':record_report} if label=='local-record-layout' else
                       {'negative_field_hypotheses':negative_report} if label=='negative-typed-fields' else
                       {'byte_stride_hypotheses':stride_report} if label.startswith('semantic-byte-stride-') else
                       {'stack_byte_subfield_hypotheses':subfield_report} if label=='stack-byte-subfields' else
                       {'byte_pointer_hypotheses':byte_report} if label=='call-bound-byte-pointer-view' else
                       {'pointer_copy_cleanup':spill_report} if label=='dead-pointer-copies' else
                       {'byte_cursor_hypotheses':cursor_report} if label=='unknown-byte-cursors' else
                       {'address_unit_hypotheses':address_report} if label=='binary-address-units' else
                       {'address_only_global_hypotheses':address_global_report} if label=='stored-symbol-addresses' else
                       {'indexed_address_hypotheses':indexed_report} if label=='measured-indexed-address' else
                       {'stack_object_hypotheses':object_report} if label=='measured-stack-object' else
                       {'partial_word_hypotheses':partial_report} if label=='measured-partial-word-fields' else
                       {'aggregate_scalar_hypotheses':aggregate_report} if label=='measured-first-scalar-member' else
                       {'wide_return_hypotheses':wide_report} if label=='binary-bound-wide-return' else None),
                action=label,run_kind='model-repair',run_config=config)
            result.normalization_candidates += 1
            normalized.append(evaluate(CandidateState(code,att,ws/(new_tag+'.o') if att.compiled else None,
                state.labels+('deterministic '+label,),state.kinds+('normalization',))))
        return normalized

    def normalize_chain(seeds, tag):
        all_states = list(seeds)
        pending = list(seeds)
        visited = set()
        added = []
        for round_index in range(config['normalization_round_limit']):
            children = []
            for index, state in enumerate(pending):
                if state.source in visited:
                    continue
                visited.add(state.source)
                children += normalize(state, f'{tag}_r{round_index}_{index}')
            if not children:
                break
            all_states += children
            added += children
            pending = _frontier([s for s in all_states if s.source not in visited],max(1,beam_width))
            if not pending:
                break
        else:
            if pending:
                result.log.append('normalization round limit reached; remaining frontier is not a fixed-point claim')
        return added

    initial = [evaluate(CandidateState(source,base,base_object_path)), *[evaluate(s) for s in initial_states]]
    initial += normalize_chain(initial,f'{name}_initial_normalize_{_digest(run_id)[:12]}')
    result.frontier = _frontier(initial,max(1,beam_width))
    best_state = result.frontier[0]
    result.best_attempt, result.best_source = best_state.attempt,best_state.source
    result.best_object_path, result.exact = best_state.object_path,workspace.repair_complete(best_state.attempt)
    for state in result.frontier:
        if state_quality(state) > state_quality(best_state):
            best_state = state
            result.best_attempt, result.best_source = state.attempt, state.source
            result.best_object_path, result.exact = state.object_path, workspace.repair_complete(state.attempt)
    if (result.exact or (compile_only and result.best_attempt.compiled
            and (result.best_attempt.frontend or {}).get('passed') is True)
            or draws <= 0 or max_depth <= 0 or max_calls <= 0):
        return result

    frontier = result.frontier
    seen = {_digest(state.source) for state in frontier}
    rejected: list[str] = []
    generation_index = 0
    asm = workspace.target_asm(ws, name)
    budget_exhausted = False
    followup, grace = None, 0
    stalled_depths = 0
    failed_plans = {}
    restart = _frontier(initial,1)[0]

    for depth in range(1, max_depth + 1):
        children: list[CandidateState] = []
        evaluated_children = 0
        parents = _frontier(frontier, beam_width)
        prior_quality = state_quality(best_state)
        restarting = resilient and stalled_depths >= 2
        if restarting:
            # Retry an intact, freshly checked initial source with another
            # representation. Keep champions; never overwrite them on reset.
            parents = [restart]
            stalled_depths = 0
            result.log.append(f'depth {depth}: stalled; restart intact attempt {restart.attempt.receipt_id} with source edits')
        if parents and not any(p.attempt.compiled for p in parents):
            parents = parents[:1]  # compose compile fixes; keep alternatives in the stored frontier
        using_grace = bool(not restarting and type_transaction and followup is not None and grace > 0)
        if using_grace:
            parents = [followup]
            grace -= 1
            result.log.append(f'depth {depth}: type-transaction lookahead from attempt {followup.attempt.receipt_id}')
        for parent_index, parent in enumerate(parents):
            pending_completion = ""
            pending_correction = ""
            if include_header_context or resilient:
                from solver import compile_obligations, project_headers
                header_context = project_headers.repair_context(repo, parent.source)
                if not parent.attempt.compiled:
                    header_context += '\nSTORAGE/TYPE RECONSTRUCTION INPUT (READ-ONLY):\n' + json.dumps(
                        compile_obligations.packet(repo, name, parent.source, asm), indent=2)
            else:
                header_context = ""
            for draw in range(draws + 1):
                if draw >= draws and not (pending_completion or pending_correction):
                    break
                if result.calls_attempted >= max_calls:
                    budget_exhausted = True
                    break
                generation_index += 1
                packet = residual.build(
                    parent.attempt, target_asm=asm,
                    target_object=ws / "target.o",
                    candidate_object=parent.object_path)
                prompt = build_prompt(
                    asm, parent.source, parent.attempt, packet=packet,
                    diagnosis=diagnosis if parent.attempt is base else "",
                    history=parent.labels, rejected=rejected, type_transaction=type_transaction)
                prompt += header_context
                planning = bool(resilient and not parent.attempt.compiled and type_plan.inventory(parent.source)
                                and re.search(r'\bvoid\s*\*',parent.source)
                                and failed_plans.get(_digest(parent.source),0) < 2 and not restarting)
                if type_transaction:
                    prompt += '\nCOORDINATED TYPE REPAIR:\n' + json.dumps(
                        transaction.packet(parent.source, asm, name, abi), indent=2)
                if planning:
                    prompt = type_plan.prompt(parent.source,header_context,abi,'\n'.join(rejected[-4:]))
                    prompt += '\nREAD-ONLY TARGET INSTRUCTIONS:\n'+asm
                semantic_repair = resilient and (parent.semantic or {}).get('status')=='observed_failure'
                if semantic_repair:
                    prompt = semantic_prompt(repo,name,parent,asm,abi,rejected,type_transaction)
                if resilient:
                    prompt += '\nDETERMINISTIC COMPILE OBLIGATIONS:\n'+json.dumps(
                        repair_context.obligations(parent.source,parent.attempt.compiler_stderr))
                if parent.semantic and not semantic_repair:
                    from solver.prompt_budget import semantic_summary
                    prompt += '\nSEMANTIC REPAIR OBJECTIVE (same frozen target-led panel):\n'+json.dumps(semantic_summary(parent.semantic))
                    prompt += '\nFix a reported failing observable; preserve input dependencies. Byte-score regressions are permitted for semantic progress. An observed pass is not all-input proof.\n'
                if strategy_brief:
                    prompt += "\nCURRENT SEARCH STRATEGY (not a proven diagnosis):\n" + strategy_brief
                completing = bool(pending_completion)
                correcting = bool(pending_correction)
                if correcting:
                    prompt += ("\nPATCH APPLICATION FAILED; CURRENT C above is unchanged.\n"
                               + pending_correction + "\nCopy each old span exactly from CURRENT C. "
                               "Correct the edit or choose a different supported hypothesis. "
                               "Do not repeat a no-op or edit declarations only present in headers.\n")
                    pending_correction = ""
                if completing:
                    prompt += ("\nOUTPUT COMPLETION: Your previous response exhausted its "
                               "budget without a final edit. Do not restart the investigation. "
                               "Select ONE supported hypothesis from the unfinished notes below "
                               "and emit only the required JSON edit against CURRENT C. "
                               "Notes are unverified hypotheses, not compiler evidence.\n"
                               + pending_completion[:6000] + "\n...\n" + pending_completion[-2000:])
                    pending_completion = ""
                from solver import stack_result_evidence
                try:
                    stack_results=stack_result_evidence.collect(repo,parent.source,name,asm,
                        (parent.attempt.frontend or {}).get('diagnostics',''))
                except (OSError,ValueError) as exc:
                    stack_results={'rows':[],'decline':str(exc)}
                    result.log.append('stack-result evidence unavailable: '+str(exc))
                if stack_results['rows']:
                    prompt += '\nREAD-ONLY STACK RESULT AND CALL ABI EVIDENCE:\n'+json.dumps(stack_results)
                workspace.assert_uncontaminated(prompt, repo, name)
                call_seed = (
                    call_seeds[result.calls_attempted]
                    if call_seeds is not None
                    and result.calls_attempted < len(call_seeds)
                    else (None if seed is None else
                          seed + depth * 10000 + parent_index * draws + draw))
                started = time.time()
                result.calls_attempted += 1
                try:
                    text, meta = provider.generate(GenerationRequest(
                        prompt=prompt, model=model, endpoint=endpoint,
                        timeout=timeout, think="low" if completing or correcting else think, num_thread=num_thread,
                        temperature=temperature, num_predict=min(num_predict, 4096) if completing or correcting else num_predict,
                        seed=call_seed, cache_dir=cache_dir,
                        cache_namespace=cache_namespace,
                        response_schema=type_plan.SCHEMA if planning else response_schema if type_transaction or completing or correcting or structured_output else None))
                except Exception as exc:
                    wall_ms = int((time.time() - started) * 1000)
                    transport = getattr(exc,'transport_events',[])
                    result.transport_events.append({'logical_call':result.calls_attempted,
                        'status':'generation-error','attempts':transport})
                    if conn is not None:
                        attempt_receipts.record_model_proposal(
                            conn, run_id=run_id,
                            parent_attempt_id=parent.attempt.receipt_id,
                            prompt=prompt, raw_response=str(exc),
                            status="generation-error", model=model,
                            sampling={"temperature": temperature,
                                      "seed": call_seed,
                                      "context_budget": getattr(exc, 'context_budget', None),
                                      "transport_events": transport,
                                      "provider": provider.provider_id},
                            wall_ms=wall_ms)
                    result.log.append(
                        f"depth {depth} draw {draw + 1}: generation failed "
                        f"({type(exc).__name__})")
                    continue
                wall_ms = int((time.time() - started) * 1000)
                result.transport_events.append({'logical_call':result.calls_attempted,
                    'status':'cache-hit' if meta.get('_cache_hit') else 'response',
                    'attempts':meta.get('_transport_events',[])})
                result.generations += 1
                result.tokens += meta.get("eval_count", 0)
                if not meta.get("_cache_hit"):
                    result.charged_tokens += meta.get("eval_count", 0)

                proposal = None
                candidate = ""
                status = "valid"
                error = ""
                plan_report = None
                if not text.strip():
                    status, error = 'empty-response', 'runtime returned no answer or reasoning text'
                    result.incomplete_responses += 1
                    if not completing:
                        pending_completion = 'The prior call returned no text. Emit the required JSON edit.'
                elif meta.get('_fell_back_to_thinking') or meta.get('done_reason') == 'length':
                    status, error = 'incomplete-response', 'output budget exhausted before finalized edit'
                    result.incomplete_responses += 1
                    if not completing:
                        pending_completion = text
                elif llm.is_refusal(text):
                    status, error = "refusal", "model refused"
                else:
                    try:
                        if planning:
                            plan = next(_objects(text),None)
                            candidate, plan_report = type_plan.apply(repo,ws,parent.source,plan,name,abi,
                                (parent.attempt.compiler_recipe or {}).get('target',''))
                            proposal = Proposal('declarations',str(plan['hypothesis'])[:1200],(),_digest(parent.source))
                        else:
                            proposal = parse_proposal(text, source=parent.source, type_transaction=type_transaction,
                                truncate_hypothesis=bool(resilient and conn is not None))
                            candidate = apply_proposal(parent.source, proposal, type_transaction=type_transaction)
                        stack_result_evidence.validate_candidate(parent.source,candidate,stack_results)
                        if type_transaction or (semantic_repair and proposal.kind != 'control-flow'):
                            try:
                                transaction.validate(parent.source, candidate, name, abi)
                            except ValueError as exc:
                                if semantic_repair and 'control structure' in str(exc):
                                    raise ValueError(str(exc)+'; line slots replace ONE physical line, not an enclosing block. '
                                        'For this value repair edit the assignment lines themselves; leave if/else/braces intact.') from exc
                                raise
                    except ValueError as exc:
                        status, error = "invalid", str(exc)
                        if proposal and any(edit.old not in parent.source and
                                (edit.old in asm or edit.old in (parent.attempt.diff or ""))
                                for edit in proposal.edits):
                            error += ("; proposed old span is READ-ONLY ASSEMBLY, not C. "
                                      "Change C expressions/declarations/lifetimes to influence code generation; "
                                      "the controller cannot patch instructions or stack offsets directly")
                        result.invalid_proposals += 1
                if candidate and _digest(candidate) in seen:
                    status, error = "duplicate", "source already evaluated"
                if retry_invalid and status in {"invalid", "duplicate"} and not (completing or correcting):
                    pending_correction = "Verifier: " + error + "\nRejected proposal (not applied):\n" + text[:5000]

                edits_json = ([asdict(edit) for edit in proposal.edits]
                              if proposal else [])
                proposal_id = None
                if conn is not None:
                    proposal_id = attempt_receipts.record_model_proposal(
                        conn, run_id=run_id,
                        parent_attempt_id=parent.attempt.receipt_id,
                        prompt=prompt, raw_response=text, status=status,
                        model=model, kind=proposal.kind if proposal else "",
                        hypothesis=proposal.hypothesis if proposal else "",
                        edits=edits_json,
                        sampling={"temperature": temperature,
                                  "seed": call_seed,
                                  "done_reason": meta.get("done_reason"),
                                  "context_budget": meta.get('_context_budget'),
                                  "transport_events": meta.get('_transport_events',[]),
                                  "fell_back_to_thinking": bool(meta.get('_fell_back_to_thinking')),
                                  "phase": "output-completion" if completing else "edit-correction" if correcting else "repair",
                                  "provider": provider.provider_id,
                                  "cache_hit": bool(meta.get("_cache_hit")),
                                  "cache_key": meta.get("_cache_key")},
                        wall_ms=wall_ms,
                        token_cost=meta.get("eval_count", 0))
                if status != "valid" or proposal is None:
                    if planning:
                        failed_plans[_digest(parent.source)] = failed_plans.get(_digest(parent.source),0)+1
                    rejected.append(error)
                    result.log.append(
                        f"depth {depth} draw {draw + 1}: {status} ({error})")
                    continue

                seen.add(_digest(candidate))
                action = f"{proposal.kind}: {proposal.hypothesis}"
                child_tag = f"{name}_modelrepair_{_digest(run_id)[:12]}_{generation_index}"
                att = workspace.score(
                    ws, repo, child_tag, candidate,
                    conn=conn, func=name, iteration=depth,
                    strategy=f"modelrepair-d{depth}", model=model, prompt=prompt,
                    temperature=temperature, wall_ms=wall_ms, run_id=run_id,
                    token_cost=meta.get("eval_count", 0),
                    extra={"seed": call_seed, "proposal_id": proposal_id,
                           "type_plan":plan_report,
                           "proposal_kind": proposal.kind,
                           "provider": provider.provider_id,
                           "parent_residual": packet.to_dict()},
                    raw_response=text, extract_status="structured-edit",
                    done_reason=meta.get("done_reason", ""),
                    parent_attempt_id=parent.attempt.receipt_id,
                    relation="model-repair", action=action,
                    feedback=packet.render(), run_kind="model-repair",
                    run_config=config)
                if conn is not None and proposal_id is not None and att.receipt_id:
                    attempt_receipts.link_model_proposal(
                        conn, proposal_id, att.receipt_id)

                result.log.append(
                    f"depth {depth} {proposal.kind}: "
                    f"{'no compile' if not att.compiled else f'{att.score:.3f}'}")
                result.compiling_children += int(att.compiled)
                evaluated_children += 1
                state = evaluate(CandidateState(
                    candidate, att,
                    ws / f"{child_tag}.o" if att.compiled else None,
                    parent.labels + (action,),
                    parent.kinds + (proposal.kind,)))
                children.append(state)
                normalized = normalize_chain([state],child_tag)
                children.extend(normalized)
                if normalized:
                    result.compiling_children += sum(s.attempt.compiled for s in normalized)
                    state = max([state,*normalized],key=state_quality)
                    candidate,att = state.source,state.attempt
                    result.log.append(f'depth {depth}: deterministic normalization -> attempt {att.receipt_id}, compiled={att.compiled}')
                if type_transaction and not att.compiled:
                    if using_grace and grace > 0:
                        followup = state
                    elif not using_grace and _quality(att) < _quality(parent.attempt):
                        followup, grace = state, transaction.LOOKAHEAD
                result.frontier = _frontier(frontier + children, beam_width)
                if (workspace.repair_complete(att) or (compile_only and att.compiled
                        and (att.frontend or {}).get('passed') is True)):
                    result.best_attempt, result.best_source = att, candidate
                    result.best_object_path = state.object_path
                    result.exact = workspace.repair_complete(att)
                    result.frontier = [state]
                    return result
                if state_quality(state) > state_quality(best_state):
                    best_state = state
                    result.best_attempt, result.best_source = att, candidate
                    result.best_object_path = state.object_path
                if not att.compiled:
                    rejected.append(
                        f"{action} -> {compile_feedback(att)}")
                else:
                    rejected.append(
                        f"{action} -> compiled; score {parent.attempt.score:.3f} "
                        f"to {att.score:.3f}; exact={att.exact}. "
                        "This is an experiment outcome, not a proven general rule.")
                if verbose:
                    print(f"      model d{depth} {proposal.kind:15} "
                          f"{att.score:8.3f}", flush=True)
            if budget_exhausted:
                break

        if budget_exhausted:
            result.log.append(f"model-call budget exhausted at {max_calls}")
            break

        if evaluated_children == 0:
            result.log.append(f"depth {depth}: no new evaluated children")
            if not exhaust_budget and not type_transaction:
                break
        # Parents remain eligible: a second group of proposals from the best
        # state is often more useful than forcing the search down a bad child.
        frontier = _frontier(frontier + children, beam_width)
        stalled_depths = stalled_depths+1 if state_quality(best_state) <= prior_quality else 0

    if type_transaction and grace > 0 and followup is not None and followup not in result.frontier and beam_width > 1:
        result.frontier = result.frontier[:beam_width-1] + [followup]
    if resilient:
        # Keep the intact root and both champions as restart candidates. Never
        # overwrite semantic progress with a byte-only winner at handoff.
        for champion in (result.best_semantic,result.best_byte,restart):
            if champion and all(s.source != champion.source for s in result.frontier):
                result.frontier.append(champion)
    return result

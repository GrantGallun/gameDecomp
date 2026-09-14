# Compiling one path at a time

`eval.reconstruct` is an opt-in development workflow for rebuilding a function
whose complete draft remains difficult to compile. It keeps the original draft,
creates a compiling skeleton, and fills one explicit unfinished region per
proposal. It uses a private copy of the complete attempt history and a private
function workspace. It does not import partial candidates into the campaign or
replace assembly in the game.

## Representation and verification

`solver.partial_reconstruction` replaces only the requested function body. The
existing signature, headers and surrounding declarations remain intact. Supported
scalar/pointer/void return signatures receive a compiling fallback; unsupported
aggregate/complex signatures decline. Broken declarations outside the function
can still prevent the skeleton compiling and are reported explicitly.

An unfinished region contains a literal `__gd_unfinished(id);` call. Its manifest
binds the original source, current source, normalized target assembly, CFG block
map, region ownership and parent revision by hashes. The model can replace one
marker with ordinary C and child markers. Every assigned target block must stay
accounted for exactly once. Target-block ownership is a hypothesis, not evidence
that the replacement implements that block correctly.

Each child is freshly compiled and checked by the configured frontend. A fixed
target-led differential panel tests the candidate. An execution earns no behavior
credit if it executes a marker, the target executes a pending block, or the trace
is incomplete. The whole execution, including any shared tail after a marker,
remains unfinished. This prevents an incorrect predicate bypassing a marker or
a dummy accidentally returning the expected answer from receiving credit. Raw
comparison outcomes remain in the receipt rather than being rewritten as passes.

Previously passing complete executions must continue passing; new observed
failures on implemented paths reject the child. A compiling structural step can
be retained before any whole path is implemented. If execution is unavailable,
its status is explicitly unvalidated and it cannot complete. Declared block counts
are never presented as verified reconstruction coverage. A byte-score regression
alone does not reject reconstruction, since adding code can change allocation.

All versions remain available for explicit branching. After all holes are gone,
the ordinary full differential panel and exact-object/frontend gates run without
unfinished-path exceptions. Nonexact full candidates are exported with their
private attempt ID for the existing `eval.agentrepair --resilient` workflow.
No automatic integration, whole-ROM success, or all-input proof is claimed.

## Running it in WSL

Use an existing bootstrapped target and a source identical to its recorded parent:

```sh
python -m eval.reconstruct init \
  --repo /home/grant/decomp/sbk1 --db /path/to/campaign.sqlite \
  --function FUNCTION --source /path/to/draft.c --parent-attempt-id ATTEMPT \
  --output /path/to/new-output --work-root /home/grant/decomp/new-private-work
```

Output and work directories must be new. Frozen held-out functions are refused.
Initialization performs no model calls. It preserves the original draft and
records both original and skeleton compiler attempts in the private history.

```sh
python -m eval.reconstruct advance --state /path/to/new-output/state.json \
  --max-calls 2 --model gpt-oss:20b --endpoint http://172.28.32.1:11435 \
  --model-lock /home/grant/decomp/campaign-workers-20260911/model.lock
```

When sharing the active campaign's GPU, use its exact model-lock path as above.
Requests use fixed32k context, low thinking by default, and one bounded transport attempt. Model, source,
target, panel and code identities are checked; changes require a new experiment.
Saved responses precede compilation in the durable proposal ledger. Compilation
or evaluation errors retain the prior version. A crash can leave a saved proposal
without an evaluated child; the next invocation does not silently claim it ran.

`--proposal proposal.json` applies one saved structured proposal instead of making
a new model request. `--parent-version N` explicitly branches from an earlier
version; it does not delete subsequent history. With no model-call budget or saved
proposal, advance performs no generation. `state.json` identifies the current
version, attempts, call outcomes, validation status and any full-candidate export.
Eligible initial entry guards use a smaller prefix/condition response: the
controller derives branch arms/shared tail and owns all marker/block accounting.
Unsupported CFG shapes retain the ordinary region protocol.
Other model prompts contain at most8 whole target blocks/128 target instructions;
omitted blocks must remain unfinished. Oversized or unsupported regions decline.

## Branch context in ordinary differential repair

`solver.branch_context` provides bounded independent target/candidate control
histories before a mismatching call, store or return. Decisions include concrete
operands/provenance and omitted-history counts. Resolved indirect jumps are
observations, not proof of an original C switch. Histories are not assumed to
align by branch ordinal or establish static control dependence.

`semantic_lane` distinguishes same-reason failures on different executed paths,
retains source/panel bindings, and includes one passing alternative target path
when available. `modelrepair.semantic_prompt` preserves compact path context for
secondary failures too. These are diagnostic prompt changes, not verdict changes.

This implementation is in the main tree and the isolated reconstruction pilot.
The active campaign's frozen source is unchanged; merely adding modules does not
enable partial reconstruction for every large function. Pilot receipts and test
results live in `eval/results/partial-reconstruction-20260912/`.

The initial real pilot converted a failing 624-instruction popup draft into a
compiling skeleton and accepted a separately authored entry-guard split with
three unfinished regions. That original experiment recorded an unavailable
differential environment due to a synthetic symbol-stride limit. A subsequent
main-tree fix restricts that ceiling to synthetic symbols; a separate replay now
builds64 cases and correctly rejects the unfinished candidate on all64. Old
experiment pins/receipts are preserved; use a new experiment with the fixed code.
Invalid heap-handle exploration inputs remain coverage debt.
The original four local model requests yielded no accepted
replacement (two malformed ledgers, two output-budget exhaustions). The workflow
now has one real model-generated compiling guard in popup-v5, independently
compiler/frontend checked:1283 prompt tokens,501 output tokens,3.10seconds of
generation. All64 cases remain unfinished; it is a structural step, not verified
behavior or exact-byte recovery. See guard-improvement.md in the pilot directory.
Transport schemas omit costly string repetition bounds; strict local validation
still enforces them. Recorded4xx errors stop an invocation instead of repeating.

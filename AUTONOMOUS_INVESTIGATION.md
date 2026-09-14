# Autonomous investigation: implementation and validation

September 10, 2026. This is an integrated experimental workflow, not a claim
that all human decompilation work has been replaced or that matching yield has
improved. Existing legacy/evidence-v1 campaigns retain their configuration and
frozen code. Start a new campaign or explicit fork to use the new policy.

## Available workflow

```text
python -m eval.completion_campaign --repo GAME --db PRIVATE_DB --state NEW_STATE \
  --scheduler investigation-v1 --functions FUNCTION ... --model-calls 3
```

Intake and deterministic compile recovery retain their existing place. Before
proposal-only model work, the new scheduler offers an investigation visit.
Each visit counts inspection and editing against the same model-call cap.
An unchanged evidence key cannot repeat an investigation. The investigator must
inspect target evidence or its instruction diff before finishing; missing
headers alone do not satisfy that requirement. At most three
investigation visits occur per function per campaign, including changes caused
by its own hypotheses. Existing work-item budgets and resumable receipts apply.

The existing tool agent can inspect headers, residuals and history, select a
retained source, inspect binary evidence and call neighbors, compile a novel
self-contained C experiment, record competing explanations, and propose edits.
Compiler experiments use the measured TU compiler recipe, native temporary
storage, bounded subprocess timeouts and archived source/object/disassembly
receipts. They do not execute generated code or import reference C bodies.
Probe attempts share the agent's compilation cap. Candidate compilation and
semantic feedback use the existing worker; behavioral alternatives survive the
handoff alongside the byte-score champion. Source changes never become facts.

## Shared hypotheses

`solver/shared_hypotheses.py` stores observation identities, competing
explanations, support references and explicit consumers in campaign checkpoints.
It does not write the immutable KB evidence table. Only an identical global
address witnessed in binary evidence permits automatic cross-function sharing;
local register names, type names and guessed similarity do not. Consumers see
the supported alternatives in their next repair context. Retraction preserves
history, removes the claim from active context and changes consumer evidence
keys. Compiler/header observations are labeled by their actual authority.

This is hypothesis propagation, not a general inferred C type graph. It does
not automatically rewrite shared headers, prove object extents, or grant callee
execution contracts. Those changes require separate measured adapters and the
ordinary revalidation gates. Existing exact objects remain independently exact.

## Executable shared capability experiments

`--capability-tasks TASKS.json` supplies an immutable mapping from a scheduler
shared-issue key to a trusted task. Its `evidence` must equal that issue's exact
identity. Each task provides `modules`, `reproduce`, `regression`, and `transfer`:

```json
{
  "ISSUE_HASH": {
    "issue_key": "ISSUE_HASH",
    "evidence": {"function": "f", "blocker": {"status": "example"}},
    "modules": ["solver/example.py"],
    "reproduce": ["tests/test_example.py::test_motivating_failure"],
    "regression": ["tests/test_example.py::test_existing_behavior"],
    "transfer": ["tests/test_example.py::test_independent_case"]
  }
}
```

The placeholders above are a schema example, not an executable repair task.
Run a standalone task with `python -m eval.capability_repair --task TASK.json
--project PROJECT --out NEW_DIRECTORY --calls 2`.

The worker copies the implementation/tests, reproduces the failure, requests
bounded implementation edits from the local model, checks syntax, reruns the
reproduction and separate existing regression/transfer tests, and archives all
attempts. Collection errors, timeouts, or all-skipped tests are unavailable
validation, never a repair. Tests, controllers and core acceptance modules are
not editable. Changes outside scoped implementation modules invalidate the
experiment. The motivating reproduction must fail before a repair is credited.

A passing experiment produces `validated_candidate_requires_frozen_fork` and
its private code tree. It does not mutate a live campaign, rewrite game code,
or mark affected functions exact. Validation of a tool change is distinct from
subsequent success on game functions. Novel task/reproduction design and
automatic deployment of arbitrary tool changes remain future work; unsupported
issues without a trusted task remain explicit, non-executable blockers.

## Capturing and replaying runtime state

`python -m eval.runtime_capture capture --rom ROM --plan PLAN.json --port PORT
--out NEW_CAPTURE.json` reads an already stopped GDB-compatible target.
The optional host defaults to localhost. The adapter does not launch an
emulator, resume a game, set breakpoints, or infer a stub's register layout.

A plan declares `function`, `architecture: "mips-o32-be"`, `byte_order: "big"`,
`entry`, `code_size`, `rom_offset`, `rom_sha256`, an explicit `registers` mapping
(`name -> {number, bytes}`), and nonoverlapping `ram` windows
(`{name,address,size,kind}`). Register coverage includes integer registers,
PC, HI and LO; noncanonical 64-bit values decline. RAM is restricted to cached
N64 RDRAM, bounded to eight MiB; MMIO is not read. Both register state and RAM
are reread to reject movement during capture. Runtime instructions must match
the supplied ROM window. Checksums bind the complete stored capture.

Replay:

```text
python -m eval.runtime_capture replay --rom ROM --repo GAME \
  --capture CAPTURE.json --target TARGET.s --candidate CANDIDATE.s --out NEW_REPLAY.json
```

Replay independently reassembles the interpreted target using the existing
ROM binder. It supports integer leaves in explicit RAM windows; calls, FPU and
64-bit operations decline. Missing memory never becomes zero-filled storage.
Return and persistent-memory differences use the ordinary differential gates.
The result is one sampled execution under supplied RAM/stack assumptions,
not full-device emulation, universal equivalence, or an exact-match certificate.

Campaign use: `--runtime-captures CAPTURES.json`, mapping function names to
lists of capture paths relative to the manifest. The complete checked records
join the campaign configuration. Captures add to the existing semantic panel;
failures are retained and incomplete execution is not promoted to a pass.

No N64 emulator/debugger endpoint was available in the inspected WSL setup.
The protocol and ROM-bound replay have automated coverage; actual game-state
capture and automatic scenario discovery are not yet demonstrated.

## Finishing and measurement

`--cleanup-exact` runs the existing certificate-preserving readability pass on
exact candidates, before optional `--integrate`. Every retained cleanup needs
fresh object-section and frontend certificates. Existing TU preparation,
batch isolation and full-ROM verification remain the integration authority.
This is conservative local cleanup, not recovery of original names/comments.

`eval.investigation_pilot` compares proposal-only and investigation workers on
fixed saved sources with the same model-call/output caps, seeds, rotating arm
order, private databases, and native temporary workspaces. Source candidates,
compiler artifacts, model receipts and outcomes persist. It reports compiler
attempts and wall time separately; equal call caps are not equal compute or
equal numbers of proposed edits. Exposed development results are not held-out
or whole-game recovery rates.

The two-function v2 pilot found no incremental investigation gain or exact
matches. Both arms improved one source through an existing normalization. Model
transport failures confound timing comparisons; its premature-finish behavior
motivated the subsequent guard. See the pilot README for the effective seed
limitation in that frozen version. The current driver supplies explicit
per-call seeds. Validation receipts and results are summarized in `PIPELINE_MAP.md`.

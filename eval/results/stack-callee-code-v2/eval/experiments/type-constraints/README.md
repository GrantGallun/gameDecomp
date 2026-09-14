# alLoadParam type-constraint unblock

2026-09-05. Header/build-assisted development experiment. No reference function
body, prior typed scaffold, or hand-written member mapping supplied to the
automatic recovery. No game source/header, ROM, evidence/inference row, or SOLVED
designation changed. Old campaign v11 and previous failure receipts are intact.

## Question and discriminating test

The previous full-plan trial asked OSS for eleven pointer views and thirty
member mappings. It chose ALParam rather than ALWaveTable and invented members,
despite the relevant headers being present. That does not establish inability
to make the smaller underlying decision.

`eval.type_constraint_trial` tests just the named ALParam/ALWaveTable decision,
using target entry-access observations and compiler-measured header fields.
The second call additionally assumes the first argument is an ALLoadFilter.
This is deliberately a **two-choice assisted diagnostic**, not an unbiased model
benchmark, a transfer panel, or a whole-function generation experiment.

Receipt: `eval/results/alLoadParam-type-anchor-v1.json`, proposals 1691/1692.

- Both calls selected **ALWaveTable**, citing the one-byte load at offset 8
  versus ALParam's two-byte member there.
- 168 and 216 generated tokens respectively; two local-model calls, no C edits.
- The flow-assumption arm was not needed for this small choice. Two successes
  do not establish reliable reasoning on all functions.

## Machinery added

`solver/type_constraints.py` is a candidate generator, not another compiler or
source rewriter:

1. Reuse the existing included-header type packet. Clang supplies declaration
   structure/record identity, including nested structs/unions and pointer types.
2. Emit a header-only constant-array probe of member addresses, member sizes,
   and owner sizes using the **configured IDO target compiler**. Read the emitted
   big-endian object data, not a hand-coded Python layout or host-ABI offsets.
   The probe records compiler/checker recipes and object/source fingerprints.
3. Treat draft pointer assignments and offset labels as source-bound hypotheses.
   Propagate equality through stores/loads of pointer fields; compare direct
   entry-field choices against binary load/store widths. NULL assignments do
   not merge unrelated objects.
4. Prune incompatible direct-field/pointee choices to a fixed point. Preserve
   alternatives and enumerate at most eight full plans (4,096 type-assignment
   exploration bound), with truncation/inconsistency/decline reports.
5. Pass plans to the existing `type_plan.apply`, compile each candidate with its
   actual parent recorded, then run the existing semantic panel and retain the
   existing semantic/byte champions. No LLM is necessary for this path.

Enabled by `eval.agentrepair --resilient`, hence by new completion-campaign
workers. No new KB inference tier or learned cross-function pattern was added.
Unsupported or inconsistent constraint packets fall back to the existing worker.

## What the constraints found

The original source's `filter->unk28 = param` and subsequent pointer loads tie
the table locals together. The supplied header layouts and observed accesses
reduce their domains to:

- `filter`: ALLoadFilter.
- `param`, `temp_v0_2` through `temp_v0_5`: ALWaveTable.
- `temp_a0`: ALADPCMBook.
- Three loop-view groups: ALADPCMloop or ALRawLoop, still ambiguous.

The eight combinations all compile. No function name or known-good member map
is hardcoded into the production constraint generator. The small diagnostic
driver is explicitly specific to this development question.

## End-to-end recovery and audit

Both zero-model replays start from **original intact m2c attempt 29535**, SHA
`d543725ea1e7bb616169e01673d15293a0cc64e97f42ed6ccc42d12358380c2b`.
The existing metadata-only stage restores includes, header signature spelling
and the recognized scoped return policy. It does not import the official body.

| Receipt | Result |
|---|---|
| `eval/results/alLoadParam-type-constraints-zero-v1.json` | Eight generated candidates, all compiler/frontend passing; zero model calls. Attempts 29675–29682 share actual parent 29674, the fresh context-projected draft. Selected 29682. |
| `eval/results/alLoadParam-type-constraints-audit580-v1.json` | Post-selection replay of 29682 on the existing 580-case DEV audit: **580/580 pass**. These cases were not candidate-generation inputs. |
| `eval/results/alLoadParam-type-constraints-zero-v2.json` | Final wiring replay including explicit constraint counts/layout receipt links: **8/8 compile**, zero model calls, selected 29693 reproduces the same source hash and result. |

Selected source SHA:
`be80e2f39a543376f044c4c58447b7e3033e1798b401e43e40225109bf0e5ead`.
Export: `eval/results/alLoadParam-type-constraints-zero-v2.best.c`.

- Automatic panel: **64/64 pass**; full-entry comparisons include v0, persistent
  memory, call arguments and modeled call-time memory.
- Both panels cover **109/109 modeled reachable target instructions** and
  **24/24 feasible conditional outcomes**. Two impossible division guards are
  explicitly excluded by the existing coverage model.
- Target and candidate text are both **480 bytes**, with **72 positional byte
  differences** (408 equal bytes). Both instruction inventories contain 117.
- Weighted progress score **91.897**, **not** percent bytes exact.
- Object verifier: **nonexact**. Residual classifier reports mostly register
  differences plus ordering/control-shape differences; no claim that register
  renaming alone would solve it.

## Limits and interpretation

This unblocks compilation and sampled behavior on the motivating function.
It supports a representation/decomposition bottleneck rather than a blanket
claim that OSS cannot understand the function. The recovery itself is mostly
m2c plus header constraints plus compiler checks; do not attribute the zero-model
result to autonomous OSS reconstruction.

The winning source uses raw-loop views even in an ADPCM path and preserves
byte arithmetic for a copied state tail. All eight view combinations compile;
tests do not establish the correct original union arm, object extent, or general
C aliasing/lifetime validity. `alCopy` is still opaque, not executed concretely.
Legacy unspecified C returns remain scoped build context; binary v0 was checked,
not ignored. Full finite branch coverage is not universal semantic proof.

The solver searches ordinary header-field/pointer views. Cast-based accesses,
arrays/bitfields, reused pointer epochs and types absent from the supplied packet
can need other representations. An inconsistent packet is a dialect-limited
decline, not evidence that the original function cannot be reconstructed.

Tests include propagation, binary width discrimination, union alternatives,
bounded enumeration, NULL separation, AST nesting, unsupported typed roots,
actual worker activation/parent lineage and compiler-probe unavailability.
Full suite after wiring: **1,156 passed, 9 skipped**.
No heldout generalization or whole-ROM integration claim is made.

To reproduce under WSL with a **new** output name:

```sh
python3 -m eval.agentrepair --repo /home/grant/decomp/sbk1 \
  --db /home/grant/decomp/kb-sbk1.sqlite --function alLoadParam \
  --attempt-id 29535 --out eval/results/alLoadParam-type-constraints-NEW.json \
  --resilient --type-transaction --include-header-context --structured-output \
  --draws 1 --depth 1 --beam 3 --max-calls 0 --seed 20260907
```

# Resilient repair wiring and bounded development replays

2026-09-05. This records implementation **and failures**, not a new autonomous
match count. All paths below are relative to `eval/results/`. Old v11 campaign
and assisted receipts remain unchanged. No game C/header, ROM, evidence/inference
row, or SOLVED designation was changed. Candidates remain isolated experiments.

## Implemented workflow

The completion campaign enables `agentrepair.run(resilient=True)`; standalone
users opt in with `--resilient`. The worker now connects:

1. Conservative TU include/diagnostic metadata projection and header ABI return
   recovery. Only known balanced legacy-return scope is restored; the reference
   implementation body is never supplied to the model or candidate.
2. Connected header type retrieval and structured pointer/member plans. The
   controller acts on current-source member occurrences, preserves the public
   parameters with typed local views, and frontend-checks field-offset probes.
   Invalid, incomplete, oversized, or layout-rejected plans remain rejected.
3. Existing C89/do-while normalization after syntax/helper failures, including
   model-generated children. Each variant is separately compiled and linked to
   its actual failed parent. No initializer is intentionally replaced by zero.
4. Existing target-led differential machinery after compiler/frontend success:
   up to 5,000 coverage probes, retained completed coverage cases, then a stress
   panel with a 64-case floor. Same frozen panel for every child; candidate
   behavior does not select the inputs. Each report records source/panel hashes.
5. Separate byte/semantic champions. Same-panel behavioral ranking precedes
   byte progress for compiling nonexact candidates. Both champions survive
   handoff and are freshly compiled/tested on reuse. An exact object/frontend
   result remains the termination condition, not a sampled semantic pass.
6. Rejected-plan representation switch and bounded intact-root restart. After
   two rejected plans for a source, try source edits; after two stalled depths,
   retry an intact verified initial state. Existing budgets still apply.

Behavioral repair prompts lead with failing observables, the existing executed
operation-DAG summary, current C and actual included type definitions. Up to
three distinct failure classes are reported. Return mismatches include the
actual return-register producers. Non-control-flow semantic edits reuse the
type-transaction ABI/control guards; malformed edits to an `if` header get a
specific physical-line-versus-block explanation before compilation.

`PIPELINE_MAP.md` documents owners and exact controller wiring. This is an
adapter to existing semantic primitives, not a second interpreter or a claim
that completion_campaign now launches differential_wavefront.

## Measurements

| Receipt | Root / budget | Result |
|---|---|---|
| `alLoadParam-resilient-unattended-v1.json` | Original intact draft 29535; 6 local-model calls | No compiling child. Type packet lacked important dependencies; plans invented/missed members. |
| `alLoadParam-resilient-unattended-v2.json` | Same 29535, no typed scaffold or hand-written hint; 6 calls | Build context recovered, all required wave/loop header types present, representation switch and intact restart executed. Still no compiling child: 5 proposals rejected; one applied child did not compile. Selected context attempt 29654. |
| `alLoadParam-resilient-c89-v1.json` | Previously **assisted typed** child 29636; zero model calls | Automatic normalization produced compiling/frontend-passing 29662, score 80.259. Automatic panel: 54 pass / 10 fail, all return-register mismatches. This validates wiring, not autonomous type reconstruction. |
| `__osBlockSum-resilient-transfer-v1.json` | Existing DEV candidate 29619; zero model calls | 35/64 passes; 53/59 instructions, 5/6 branch outcomes. First reported disagreement is a stack-buffer pointer passed to an opaque call. Not yet classified as a source bug versus a harness/ABI modeling issue. |
| `alSynSetFXMix-resilient-transfer-v1.json` | Existing DEV candidate 29598; zero model calls | 58/64 passes. Actual target integer value 1 versus candidate float bits 0x3f800000 at output offset 0xc. |
| `alSynSetFXMix-resilient-repair-v1.json` | Same DEV root; 2 calls | Byte-first prompt with appended semantic report chased parameter signedness; conflicting-ABI child and duplicate. No improvement. |
| `alSynSetFXMix-resilient-repair-v2.json` | Same root; 2 calls; semantic-first/value-DAG/header packet | Model correctly diagnosed the integer/float store, but replaced an `if` header as if its slot covered the whole block. Both children failed compilation. Diagnosis improved; actuation did not. |
| `alSynSetFXMix-resilient-repair-v3.json` | Same root; 2 calls; assignment-level instructions and control guard | Both children compiled. Selected semantic champion 29667: 58/64 passes, score 53.442. Byte champion 29668: only 37/64 passes despite score 74.806. The behavioral regression was **not** selected. Still no increase in passing-case count. |
| `alSynSetFXMix-resilient-handoff-v1.json` | Start from byte champion 29668, retain v3 alternatives; zero model calls | Freshly selected 29670 reproduces semantic champion source hash and 58/64 passes at lower score 53.442. Cross-job retention works on real compiler/runner artifacts. |
| `alLoadParam-resilient-assisted-control-v1.json` | Previously **assisted** repaired source 29647; zero model calls | Fresh reverify 29672 passes 64/64 automatically generated cases with 109/109 instructions and 24/24 feasible outcomes covered; score 78.621, nonexact. Confirms the new panel distinguishes the known return repair from the normalized predecessor. |

The original automatic exploration allowance of 256 probes reached only 42/109
modeled instructions and 13/24 feasible branch outcomes on alLoadParam. Raising
the existing explorer to 5,000 probes reached **109/109 and 24/24** in roughly
two seconds locally, with no model or manual seed. Sixteen retained coverage
cases remain in the 64-case stress panel. The two impossible division guards
are explicitly excluded by the existing coverage model, not silently counted
as covered. The normalized candidate's 10 failing tests confirm that full
coverage is not the same thing as matching behavior.

alSynSetFXMix remains partial at 34/36 modeled instructions and 5/6 branch
outcomes. Its missing branch follows an unsigned byte load and signed-negative
test; no new infeasibility proof was implemented here. Calls are opaque,
including an indirect callback with unresolved concrete arity/implementation.
These tests are diagnostic, **never authoritative all-input equivalence**.

## Audit and limits

- Full suite: **1,147 passed, 9 skipped**. Added regression tests exercise actual
  normalization and lineage, complete mapping validation, malformed input,
  layout-probe rejection, header dependency budgeting, panel identity, live
  interpreter return feedback, semantic ranking, strategy switching, and the
  value-edit block-mangling rejection/correction path.
- No LLM trial above is a new byte-exact match. Type reconstruction from the
  original alLoadParam draft remains unresolved. Do not seed a campaign with
  its assisted solution and label that autonomous recovery.
- The value/type hypotheses still need stronger source binding. The third
  FX-mix trial edited the wrong branch, then removed an assignment rather than
  writing the correct value. The higher-byte/poorer-behavior child was retained
  as a separate byte experiment, not accepted as behavioral progress.
- Header-offset probes currently check member location, not the full semantic
  type/storage-extent/alias contract. A variable reused for incompatible views
  may require epoch splitting or a more expressive structured plan.
  Member-plus-literal arithmetic is conservatively declined unless the draft
  itself supplies a void-pointer-local use proving the original byte unit; an
  unknown scalar member must not be blindly recast to a pointer.
- The standalone A/B trials used explicit saved source IDs and fresh outputs;
  they are development engineering, not a frozen heldout benchmark. Prompts,
  raw outputs, rejected plans, compiler children and actual parent links are in
  the attempt/proposal ledger. Campaign-level code pinning remains separate.
- No cross-function generator/pattern was promoted from these observations.

## Reproduce a new bounded worker trial

From `/mnt/c/Code/gameDecomp` under WSL, choose a **new** output name:

```sh
python3 -m eval.agentrepair --repo /home/grant/decomp/sbk1 \
  --db /home/grant/decomp/kb-sbk1.sqlite --function alLoadParam \
  --attempt-id 29535 --out eval/results/alLoadParam-resilient-NEW.json \
  --resilient --type-transaction --include-header-context \
  --structured-output --retry-invalid --draws 1 --depth 6 --beam 3 \
  --max-calls 6 --num-predict 6000 --timeout 420 --seed 20260906
```

For zero-model component checking use attempt 29636 and `--max-calls 0`, but
retain the explicit **assisted-root** label. Continuing the 24-function campaign
requires a new explicit fork; never rewrite/resume v11 against changed code.

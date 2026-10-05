# Frontier audit: where an LLM can change the search

Read-only assessment on 2026-09-27. The active campaign was neither stopped nor
reconfigured. `audit.py` produced `audit.json` from checkpoint 31567 and the
completed-job log tail. The checkpoint is immutable; the campaign continues.

## Measured yield

From baseline checkpoint 30125 to 31567: 720 completed jobs over approximately
110.5 minutes, 33,648 compilation cache misses, nine added exact functions,
zero exact losses, and **zero model calls**. Only three of the nine additions
were absent from both earlier raw exact ledgers. The other six reproduce prior
results. This is an assisted development campaign, not a clean generalization
evaluation.

The log tail had advanced to 721 jobs when read: 527 (73.1%) left the accepted
byte score unchanged. Some are validation work, so flat score does not imply
zero useful information. Score improvement is also not proof of exactness or
semantic progress. Register search accounts for 28,003 compilation misses and
six exact results; binary-type drafting for 1,018 and two; operand repair for
2,533 and one. Per-profile details are in `audit.json`.

149 functions have a model profile as their next eligible action. The running
deterministic-only dispatcher excludes those actions. Configured model budget
is three calls; it has not been rewritten to zero. The current run therefore
does not measure the effectiveness of the existing hybrid solver.

The separate `python -m eval.status` view, read later, reported 1,005 exact of
2,013 attempted: 793 SOLVED, 109 reconstructed-header-assisted, 103
reference-type-assisted, zero recovered-from-target-source. Its scope differs
from the campaign's 1,012 object-exact/integrated plus 24 function-exact pending
integration. These counts must not be added or treated as the same denominator.

## Remaining work at checkpoint 31567

These lanes partition the 1,015 unresolved functions:

| Lane | Functions | Required advance |
| --- | ---: | --- |
| Byte matching | 496 | Better source constructions and compiler experiments |
| Observed behavior failure | 245 | Repair against executable counterexamples |
| Execution environment | 140 | Usable target cases, supported ABI/hardware execution |
| Frontend | 39 | Compilable, admitted C and declarations |
| Validation | 52 | Validate the current source revision |
| Parked | 43 | Backend/intake engineering |

The parked group contains 19 hardware-backend cases, 10 operational/intake
failures, eight SDK control-flow/relocation limitations, and six object
postprocessing limitations. Among unsolved functions, 341 have an observed
semantic pass with execution debt; these are not additional functions and are
not full semantic proofs. Ninety-three report no completed target cases.
Twenty-four already function-exact cases still await integration; exact object
verification is not a whole-ROM certificate.

## Missing or underused LLM applications

1. **Design compiler experiments.** Have the model identify competing causes,
   request candidate compiler-phase output, and propose discriminating source
   interventions. Target internal compiler traces are unavailable; actual
   candidate traces and final target assembly are available evidence. Phase
   inspection tools exist but are not wired into ordinary model repair.
2. **Construct different source representations.** Rebuild a loop, branch
   structure, temporary lifetime, or calling/interface hypothesis when local
   mutations stall. Use jointly checked caller/callee evidence for ABI/layout
   hypotheses. Shared context and type transactions already exist; this is an
   expansion of them, not a claim that all cross-function support is missing.
3. **Remember negative experiments and allocate effort.** Retrieve which
   interventions failed, on which source/compiler/evidence revisions, and why.
   Current logs are durable, but normal repair does not retrieve their full
   causal history. Avoid repeating a family without new relevant evidence.
4. **Repair shared capabilities.** Cluster residuals and commission a bounded
   generator, parser, harness, or backend change. An opt-in investigation and
   capability-repair path exists; the default controller does not close this
   loop. Require a motivating positive test and independent transfer cases
   before promoting any proposed pattern. Miner passes remain deterministic.
5. **Build valid execution cases.** Propose inputs, captures, and harness changes
   for environment-blocked functions; validate them on the original binary.
   A model's invented expected output must never become the oracle.

The normal model prompt already includes target assembly, candidate C,
compiler diagnostics and residual feedback. Adding feedback alone is not the
missing feature. The three-call budget includes malformed/duplicate outputs
and retries. Investigation additionally requires an inspection before its
first patch. Ordinary patches are limited to four edits and bounded size;
large compiled functions can also hit prompt limits. Persistent experiment
memory, useful phase observations, and separately budgeted tool interaction
need improvement before scaling this path across the cohort.

Relevant frozen implementation: `solver/modelrepair.py` (prompt, patch bounds,
retry accounting), `solver/toolagent.py` (inspection/patch action loop),
`solver/investigation.py` (opt-in probes), `solver/repair_queue.py` (routing),
and `eval/completion_campaign.py` (retained candidates, shared context, budgets).
All are under `eval/results/resume-pipeline-20260908/code/`.

## Recommended next experiment

Select 24 unresolved functions stratified across byte, semantic, frontend and
environment failures, excluding previously known exact candidates. Compare
the same starting sources in three arms: current deterministic search,
existing model repair, and a tool-using investigation with compiler-phase
observations and retrieved failed experiments. Give each a common compiler
and elapsed-time ceiling; record model tokens/cost separately. Allow the third
arm 8-12 model/tool turns so inspection does not consume its entire opportunity
to edit. Preserve every attempt, including invalid outputs and timeouts.

Measure newly exact objects, independently confirmed semantic repairs, usable
new execution cases, compiler calls, wall time and cost. Use source/header
provenance labels and forbid held-out source. Promote a reusable mutation only
after it fires on its motivating case and has passed broader controls. Keep
the global exact-match ratchet and integration gate.

The branch-default result in `../direct-compiler-20260926/RESULT.md` demonstrates
the proposed loop: direct phase evidence, a new source construction, independent
exact verification, then a cheap deterministic generator. The live campaign
has now reproduced that result. One case does not establish broad transfer.
The failed compiler-effect predictor should not be revived without new evidence.

Forward compilation being deterministic does not make its inverse search easy.
These measurements establish limited yield from the current search policy;
they do not establish that deterministic matching is impossible or that an LLM
will necessarily do better. The paired experiment should decide the latter.

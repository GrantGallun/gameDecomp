# Runtime state, directed paths, and callee reuse audit

Read-only audit, September12. No campaign process, model, code pin, game source,
or database was changed. This audit distinguishes available components from
workflows demonstrated on a running game.

## Existing capability

| Idea | Implemented here | Important limit |
|---|---|---|
| Capture real entry state | `solver/runtime_capture.py`, `eval/runtime_capture.py`: explicit GDB register map and bounded RAM windows; verifies stopped PC, double-reads state, binds instructions to ROM, hashes receipt | Requires an already stopped compatible endpoint. No launch, breakpoint automation, scenario controller, or automatic register/RAM plan discovery |
| Replay captures during repair | `eval/captured_panel.py`; campaign `--runtime-captures` validates and stores function-specific records, adds their observations to ordinary semantics | Capture replay explicitly rejects every call, FPU operation, and64-bit operation. It currently supports integer leaves only |
| Target-guided branching | `mips_differential._predicate_mutations`, `_invert_slti_origin`, `_effective_input_origin`, `explore_coverage` | Solves selected simple equality, zero, sign, and less-than guards from observed origins. There is no general symbolic bit-vector execution or full prefix-constraint solver |
| Preserve path context | `branch_context`, semantic failure grouping, partial reconstruction manifests | Provides executed decisions and pending blocks; does not prove feasibility or recover all original control flow |
| Reuse callee semantics | `callee_execution` and `linked_callee`: independently ROM-bound integer leaves and closed compiler word-pair helper dialects; shared memory and bounded ABI checks | Executes observed binary implementations. It is not a learned function-summary engine, nested-call execution engine, or general emulator |
| Shared explanations | `shared_hypotheses` propagates supported, retractable alternatives to consumers sharing observed global addresses | Hypotheses are not executable memory/callee contracts or trusted types |

Capture integrity and wrong-candidate replay tests exist in
`tests/test_investigation_workflow.py`. These use a simulated remote endpoint
and generated ROM fixtures; they are not evidence of a real game-state capture.

## Local tooling actually present

`runtime-tool-inventory.json` records the WSL environment: `gdb` is on PATH;
no `mupen64plus`, `simple64`, `ares`, `cen64`, or `retroarch` was found on PATH,
and no emulator/debugger process name was running. Python `z3`, `angr`, and
`unicorn` were absent in the campaign venv. This is a bounded inventory, not a
claim that those tools are absent everywhere on the machine.

Windows has **Project64 installed** at
`C:\Program Files (x86)\Project64 3.0\Project64.exe`; no Project64 process was
running when inspected. No compatible GDB endpoint or documented state-export
adapter was verified. Its installed presence improves the capture starting
point, but does not make the current GDB adapter immediately usable. No
Snowboard Kids save file was found in that installation's top-level Save folder.

## What current evidence says

The popup pilot is a real example of synthetic-environment debt: an earlier
fixed256-case target exploration returned19 cases and faulted237, often while
using random asset handles to index `gRelocatableHeapBlockStartAliases`.
The harness already reads fault origins, mutates identified inputs, and explores
new failing prefixes. Reordering those mutations did not improve completed
coverage, so those prototypes were rejected. Simply adding more random values
or putting faults first is not an evidence-backed solution.

The released stress improvement removes duplicate effective inputs: its final
combined replay selects64 distinct inputs instead of60 in64 slots, with the
same65 target trials and coverage. Distinct observed call sequences rise14to15.
That improves test diversity; it does not create coherent full-game state.
See `../impact-20260912/INPUT_DEDUP.md`.

A saved real popup entry cannot currently be fed through capture replay:
`runtime_capture.replay` would reject its calls before execution. Ordinary
`semantic_lane.Panel` and capture replay are separate execution paths; the new
ROM-bound helpers and opaque-call ordinal fix do not automatically remove the
capture path's integer-leaf restriction.

## Smallest viable pilots

1. **Real-state capture adapter, one supported leaf.** Verify the installed
   emulator's actual debugger/export interface before choosing an adapter.
   Stop at an existing ROM-bound integer leaf such as
   `getRelocatableHeapBlockBase`; preserve real handle registers and the table
   RAM plus stack in an explicit plan. First require ROM byte identity,
   stable repeated reads, target self-replay, and rejection of a known wrong
   candidate. This proves transport and real-state replay only. Collect a
   few distinct entry states afterward; do not count a single successful
   snapshot as branch coverage or game correctness.

2. **One bounded concolic slice, independently useful without an emulator.**
   Pick a missing edge reached by an existing completed target seed. Preserve
   that seed's earlier observed guards, and solve only a bounded integer
   backward slice with actual MIPS32 widths, shifts, sign extension, and
   mapped-address bounds. Keep unsupported operations/calls explicit. Feed
   each proposed assignment back into the ordinary unintervened target
   runner; only actual new completed edges count. Compare against today's
   mutation strategy at the same256 trials and total instruction budget.
   Installation of a solver alone is not implementation of this machinery.
   Start with one reproducible derived-guard miss rather than whole-game SMT.

3. **Captured caller extension after the leaf pilot.** To address the popup,
   make capture replay accept an explicit, pinned callee environment and
   call ABI contracts, retaining captured address-keyed memory. Admit only
   supported ROM-bound callees initially. Remaining opaque/device effects
   must stay labeled assumptions or blockers; an entry snapshot does not
   contain future I/O, DMA, callbacks, or other-thread effects. This is the
   missing bridge between real memory and today's differential caller tests.

For a callee-summary throughput pilot, first profile which already-supported
leaf consumes interpreter time. If one dominates, memoize its observed
return/write transcript only under the same ROM/helper identity, entry ABI,
and complete read-dependency memory values, with alias/extent validity and
termination preserved. Compare cached and uncached execution on held-out
inputs and retain uncached transfer tests. A summary inferred from a handful
of executions cannot safely substitute for arbitrary future calls; predicted
summaries should remain hypotheses until independently checked.

The immediate recommendation is to prove one real capture and one bounded
derived-guard solver separately. Their metrics answer different questions:
real captures reduce invalid environment inputs, while concolic proposals aim
to reach specific missing branches. Combining both is valuable only after
each yields reproducible, uncompromised ordinary replay evidence.

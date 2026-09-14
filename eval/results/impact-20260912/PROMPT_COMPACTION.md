# Semantic prompt headroom recovery

Recent model receipts showed pre-request context guard failures rather than a
broken model server. The request budget includes a conservative UTF-8-bytes/2
estimate, the unchanged 1,024-token template reserve, response schema and the
full 6,000-token answer allowance. The model remains at fixed 32,768 context.

The semantic repair prompt mixed required causal evidence with verbose nested
callee trace excerpts, repeated compiler invocation recipes, and repeated JSON
field names. Executed branch context and extra helper contracts increased an
already substantial prompt. For training receipt 3866, the complete prompt was
62,451 characters; its semantic packet alone occupied roughly 31,420 characters.
Some newer packets reached approximately 53,000 characters before C and assembly.

`prompt_budget.semantic_packet` now makes a prompt-only projection:

- Keep primary input, reasons, causal feedback, all executed guards and operand
  values/provenance, passing contrast, source/panel identity, ABI facts and debt.
- Replace nested concrete-callee trace prefix/suffix excerpts and duplicated
  compiler invocation/includes with exact canonical-JSON hashes, byte lengths
  and observation counts. Callee identity, arguments, results/errors, ABI
  violations and execution limits remain visible.
- Losslessly tabulate dictionaries sharing the same field set. Every column is
  named; different shapes preserve missing versus null fields and unknown keys.
- If the base semantic prompt exceeds 48,000 bytes, omit whole secondary
  counterexamples with exact content references and counts. The complete
  primary failure and its passing contrast remain. Full C, assembly and header
  definitions are untouched.

This preference does not admit a request: the unchanged guard still checks the
final prompt including search suffixes, schema and output allowance. Full
semantic receipts and all evaluator gates remain unchanged.

## Saved-prompt replay and live local canary

The most recent 50 saved receipts contain 40 semantic prompts. With the exact
base-function fallback boundary (before search appends obligations/strategy),
28 were oversized before the change and **19 now fit**. Nine larger requests
remain guarded; there is no arbitrary slicing to force them through.

For the formerly rejected `initTrainingCourseRace` receipt **3866**, the canary
calls the actual `semantic_prompt` builder, reconstructs its exact recorded raw
packet, uses its recorded header assistance, and preserves the original complete
source, assembly and appended suffix. Assertions bind those inputs before the
single model request:

| Measure | Before | After |
|---|---:|---:|
| Prompt characters | 62,451 | 49,794 |
| Estimated total tokens, including 6,000 output | 38,597 | 32,268 |
| Context allocation | 32,768 | 32,768 |

The local Ollama canary completed in 61.78 seconds, reporting **18,522 actual
prompt tokens**, **1,350 generated tokens**, and `done_reason=stop`. Its complete
response and metadata are retained in `prompt-compaction-canary.json`; no edit
was applied, compiled, imported, or promoted. This establishes usable generation
headroom, not improved source correctness.

The initial automated approval review treated the WSL host IP as an unverified
external destination. Fresh Windows adapter/listener checks established that
172.28.32.1 is this computer's `vEthernet (WSL)` address and port 11435 belongs to
local Ollama PID 25536. The exact same canary was then approved and executed
under the shared GPU lease. No destination or execution workaround was used.

Validation: **40 focused tests pass**, covering primary evidence preservation,
exact omission references, lossless ABI tables, unknown/null fields, whole-case
fallback, complete C/ASM preservation, and continued rejection of irreducible
oversized prompts. Root owns final full-suite testing and release installation.

Files: `solver/prompt_budget.py` (new projection), the `semantic_prompt` function
in `solver/modelrepair.py`, and `tests/test_semantic_prompt_compaction.py`.
`replay-prompt-compaction.py` preserves the final suffix but bases the 48k
decision on the actual semantic function's boundary. The canary independently
uses the real function itself and checks exact raw packet reconstruction.

# Deterministic code-shape search

`solver.code_shapes.candidates(source, function, max_variants=24)` generates
alternative implementations without a model or reference source:

- braced `while` to `for`, or a guarded `do/while`;
- braced `for` to a scoped `while`, preserving the original body's scope;
- inverted `if/else` with exchanged branches;
- array-element returns to pointer expressions;
- boolean-literal conditional returns to explicit branches.

Generators are lazy, interleaved by family, source-deduplicated, and bounded.
Edits stay inside the named function. Unsupported syntax is skipped. For-loop
conversion declines continue, goto, labels and declaration initializers.
General arithmetic reassociation and mixed-type ternary expansion are not
assumed equivalent.

The differential repair pilot automatically runs these after observed semantic
checks pass, regardless of assembly residual classification. Allocation probes
retain their original classification gates. Each expansion screens at most 48
unique candidates across all families; each round expands at most four frontier
members. The frontier can retain semantically clean lower-scoring alternatives
and compose transformations. Compiler calls, semantic results, parent links,
source hashes and action labels are recorded in the existing attempt receipts.
Only the authoritative object oracle can declare a byte-exact match.

## Mismatch-guided source targeting and OSS fallback

`solver.source_attribution` captures the original IDO object immediately before
the matching helper removes `.mdebug`. It reads post-assembly address/line records
using the existing MIPS objdump, without changing compiler flags, optimization,
guards, stripping or match verification. Captured input identity, unchanged
allocated sections/relocations, every instruction address/word, and reproduction
of the entire production-normalized listing are checked before using the map.

`solver.residual_sites` joins unified-diff candidate positions directly to those
records. Repeated instruction text is disambiguated by listing position. Direct
compiler-attributed source lines take priority in candidate generation, search
ranking and OSS prompts (`DIRECT .text+address [bytes] Lline`). This is compiler
attribution, not a claim that the line is the sole causal owner of the instruction.

Target-only deletions, missing records, header locations, unsupported helpers,
changed source/objects and source-authored line remapping remain explicit gaps.
Only those gaps use operation-family syntax hypotheses and compiler interventions.
Source prefixes inserted by the workspace are handled through its existing
`#line 1 "candidate.c"` directive; stale locations never become direct labels.

Candidate sites are ranked before the generation cutoff; a relevant return near
the end of a function can displace unrelated early loop experiments. After each
compile and semantic replay, the search records the actual edited source span,
removed/added residual row IDs and outcome. This establishes compiler influence
(which may be nonlocal), not universal semantic equivalence. Remaining siblings
are re-ranked using clean compiler responses; inert variants do not suppress all
other transformations at the same site.

The JSON receipt contains `mismatch_source_maps` with numbered source sites and
`compiler_interventions`. Attempts also retain `source_attribution` and a
`<candidate>.source-lines.json` artifact with address/byte/line records, hashes and
coverage. Each attempt's `source_intervention` is also persisted
in SQLite's `source_interventions` table. Reloading evidence requires identical
parent source and residual hashes, preventing stale line numbers or evidence
from a different residual from being reused.

Once the deterministic budget is exhausted, normal mode passes flagged lines,
mismatch IDs and experiment outcomes to the configured model (default
`gpt-oss:20b`) in diagnosis, patch and retry prompts. Model alternatives go
through the existing bounded edit parser, compiler and semantic/exactness
verification; their compiler interventions are recorded too. No model request
occurs with `--deterministic-only`. An unavailable model leaves the verified
candidate and receipts intact.

Use the existing `python -m eval.differential_repair_pilot` command with
`--deterministic-only` and the usual repo, database, source, source parent attempt,
function, output and semantic-panel arguments to run without model calls.
This is a bounded search, not enumeration of all equivalent programs. Exhaustion
does not prove impossibility; passing a semantic panel is not a universal proof.

Validation: `python -m pytest tests/test_code_shapes.py
tests/test_differential_repair_pilot.py tests/test_candidate_frontier.py
tests/test_transition_policy.py tests/test_residual_sites.py tests/test_source_attribution.py -q` (put the command on one line).
The native compiler tests execute generated implementations at O0 and O2 and
cover negative/zero inputs, break, continue, variable shadowing, volatile condition
side effects and NaN. They validate transformations, not target-game match yield.
The optional MIPS assembly intervention test skips when the installed Clang has
no MIPS backend. OSS routing tests use controlled model responses to verify the
ordering, evidence prompts, successful proposals and semantic-regression rejection.
The grounded source-attribution test runs under WSL with the real IDO toolchain
and matching helper. It verifies direct line coverage, checks that an independent
unmodified build produces the same allocated object, and rejects corrupted or
stale attribution artifacts. The initial O2 loop probe mapped 30/30 instructions.

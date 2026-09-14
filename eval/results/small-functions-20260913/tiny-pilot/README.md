# Tiny-function compiler pilot, 2026-09-13

The new generic split-byte-load/zero-test rewrite restored the exact `strlen`
object from the retained checkpoint-13212 candidate. This is one measured
function, not evidence of broad corpus transfer. No canonical source, live
campaign state, model budget or deployment was changed by this pilot.

## Selection and authority

Sources came only from retained `beyond-diff-inputs` candidate artifacts and a
short read-only selected-attempt/function/TU query. No reference C was read.
Each native run initialized an empty private database with one function/TU and
its own full attempt history. The reused `pointer-loop-20260912/pilot.py` harness
explains the historical `private-pointer-loop-compiler-pilot` receipt kind and
run IDs; these runs test tiny-function shapes, not pointer-loop repair.

| Function | Selected attempt | Candidate source SHA256 | Target assembly SHA256 |
|---|---:|---|---|
| strlen | 82748 | `50a5ffa168d213012bf2346910774e590377104c2a6383d529afac9737fa907e` | `c7655274c508a12fac3ff38685793e6be7eb935c95779d0dd313acd59dc7c19a` |
| osAiGetLength | 73370 | `10f49daf55c327d6a55981ab4ea2ff0057499976d607f3026b99728534d5e084` | `9c7a7b07d9bb01c60a93bbaadae8cde65eba4a18be714123995f1da0fd77c067` |

## Measurements

All runs live under `/home/grant/decomp/tiny-pilot-20260913/`. Their complete
private attempt databases, sources, diffs, compiler logs, object files, selected
inputs, semantic receipts and archive hashes are retained under `receipts/`.
The native workspaces additionally retain complete compiler intermediates and
build-input manifests. All compiler failures remain in history.

| Run | Compiler attempts, including baseline | Compiled | Best score | Exact variants |
|---|---:|---:|---:|---:|
| osAiGetLength-r1 | 8 | 8 | 96.667 | 0 |
| osAiGetLength-r2 | 9 | 9 | 96.667 | 0 |
| osAiGetLength-fixed-view | 2 | 2 | 96.667 | 0 |
| strlen-r1 | 11 | 11 | 98.5 | 0 |
| strlen-r2 | 9 | 8 | 100 | 3 |
| strlen-final-generator | 2 | 2 | 100 | 1 |
| strlen-routed-budget8 | 5 | 5 | 100 | 1 |

There were 46 compiler attempts, 45 successful compiles, and 24 bounded semantic
evaluations. There were zero model calls. Manual exploratory variants in round 2
are recorded as compiler hypotheses, not model yield or production generators.

The controlled routing comparison uses the same maximum of eight variants.
Before the change, `rewrites.propose` emitted zero applicable variants and
`isolated_register_web` emitted three distinct candidates; all three remained
98.5%. After the change, the existing family emitted those same three plus the
new first candidate. The routed replay compiled all four, with unchanged scores
for the old candidates and a byte-exact new result. Actual counts differ because
the family stops when it exhausts distinct applicable candidates; it does not
spend unused budget on duplicates.

The final generated source SHA256 is
`68cf31775a9cb86b50d585ed7734c1655bc3847bbed631c701b98c26c546bfe9`.
`strlen-final-generator/summary.json` and `strlen-routed-budget8/summary.json`
retain `mips_object_section_certificate` results with exact allocated sections
and relocation expressions plus passed project frontend checks. The target and
candidate `.text` sections are both 48 bytes including padding and have SHA256
`9354a122c79ffd1836d06bfe8bd97339e05d85adf6d1408f1e0b1e3c5d8b9e83`.
Final source frontend hashes differ from candidate hashes because the existing
workspace build renders the candidate through its recorded prelude/build path.
The certificate binds both forms. It does not certify final link placement or
the whole ROM.

Both the baseline and exact `strlen` candidate passed 64/64 admitted cases, with
`observed_pass_with_execution_debt`. Finite synthetic execution is not universal
equivalence; the object certificate is the stronger matching authority. No
claim of real runtime capture is made here.

## Implemented mechanism and limits

`solver.byte_test_inline.candidates` removes an uninitialized byte temporary when
its only remaining occurrences are one pointer-load assignment and an immediate
zero-test read. The pointer and temporary must have the same explicit byte type
and be simple leading local declarations. It retains the loop structure, load
count and sequencing. Volatile qualifiers, local preprocessor directives,
pointer side effects, intervening statements, additional reads/address-taking,
type mismatch and detected shadow declarations decline. Included macro contents
are not resolved; the compiler and downstream checks remain required.

`principle_variants.isolated_register_web` routes the new family through its
existing source deduplication and hard candidate budget. Existing families keep
their relative order. The measured catalog entry is `split-byte-zero-test-load`.

The existing `hardware_environment.register_views` had a separate detector bug:
its broad local-shadow regex interpreted `return AI_LEN_REG;` as a declaration.
The fix admits that scalar read while preserving actual shadow rejection. Its
real encoded-address candidate compiled but did not improve `osAiGetLength`.
No hardware behavior was invented or admitted; both baseline and rewritten
candidate remain semantically unavailable for the documented hardware reason.
The failed broader address/pointer/volatile probes were not added to search.

## Reproduction

From the WSL project checkout, use an unused native output directory:

```sh
python3 -m eval.results.pointer-loop-20260912.pilot \
  --selection eval/results/small-functions-20260913/tiny-pilot/strlen \
  --variants eval/results/small-functions-20260913/tiny-pilot/strlen/variants-routed-budget8.json \
  --out /home/grant/decomp/tiny-pilot-20260913/strlen-new-run
```

The harness verifies the retained source and target hashes plus selected-row
identity before initializing its private workspace/database. It refuses an
existing output directory. No live DB is copied. Rerunning compilation creates
new receipts; it must not overwrite the accepted records in this directory.

Focused verification: 64 tests passed across byte-test inlining, existing
principle variants/retrieval, encoded register addresses, hardware obligations
and imported-pattern naming invariants.

## Missing-data consumer audit

`semantic_lane.Panel` already accepts target-compiler-measured global extents
through `# MIPS_DIFF_EXTENT`, but only probes a bounded number of globals. Its
debt text explicitly says their contents are synthetic. In
`mips_differential._seed_memory`, unspecified bytes use deterministic filler;
`Program.data_bytes`, `Program.data_words` and admitted callee data are existing
initialized-byte consumers, with concrete-address alias/conflict checks.

A reusable data catalog should feed independently ROM/ELF-bound initialized
bytes and validated extents into the target program before panel cases and
identity are generated. It must retain source artifact hashes, physical address
ownership and mutability classification; BSS, runtime-mutated RAM and devices
cannot be represented as universally constant ROM data. Case overrides remain
separate observations. Root owns that implementation; this pilot only audits
the consumer interface and does not change memory admission.

## Scoped release files

New: `solver/byte_test_inline.py`, `tests/test_byte_test_inline.py`.
Scoped additions: the new local import/candidate loop in
`principle_variants.isolated_register_web`, one catalog `register(Pattern(...))`,
the return-keyword exclusion in `hardware_environment.register_views`, and the
new positive/shadow regression in `tests/test_encoded_register_addresses.py`.
Deployment must preserve unrelated working-tree/frozen-code differences and
run normal release checks. No private DB import or budget reset is requested.

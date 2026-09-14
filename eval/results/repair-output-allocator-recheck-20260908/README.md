# Saved repair proposals with initialized allocator state

The targeted patch that the six-call pilot rejected for indentation repairs all four newly exercised failures after whitespace-only reapplication. This follow-up used **zero model calls**, kept the concrete allocator enabled, and compared the same four saved sources on the same 61-case panel.

| Source | Passed | Failed | Inconclusive | Assembly similarity | Exact |
|---|---:|---:|---:|---:|---|
| Parent | 57 | 4 | 0 | 53.442 | No |
| Saved normal patch | 61 | 0 | 0 | 74.389 | No |
| Saved full-function regeneration | 59 | 2 | 0 | 52.455 | No |
| Saved compact-prompt patch | 59 | 2 | 0 | 51.044 | No |

All four sources compile and pass the candidate frontend. Similarity is the existing assembly score, **not percentage of bytes correct**. The passing patch remains `observed_pass_with_execution_debt`; it is not a semantic proof or a byte-exact result.

## What changed in the test fixture

The earlier generated panel had 57 successful early-return cases covering only 9/36 target instructions and 1/6 conditional branch outcomes. Attempts to reach the body faulted inside the newly concrete `__allocParam`: its allocator-global pointer and free-list head were uninitialized. The old failing input used an opaque allocator return override, which no longer supplied concrete allocator state.

The target allocator assembly in `report.json` reads `alGlobals`, loads its free-list head at offset `0x2c`, reads the head's next pointer, updates the list, clears the removed head's next pointer, and returns that head. Four explicit fixtures initialize those mapped locations and use `fxmix` values 1, 127, 128, and 255. They retain the historical nonnull voice/channel fixture and have **no call-return overrides**. They are development fixtures derived from target assembly, not held-out tests.

The new panel retains the 57 generated cases and adds those four fixtures. Target coverage rises to **34/36 instructions and 4/6 conditional branch outcomes**. The normal patch covers 32/32 of its own instructions and 3/4 of its conditional outcomes. No selected case faults or remains inconclusive. Initial bounded exploration still contains rejected faulting inputs; its debt is preserved. Indirect callback arguments and effects remain incompletely modeled, and target branch coverage remains partial.

## Interpretation and receipts

The normal patch removes the redundant signed comparison and stores the unsigned input into the integer union member. The full regeneration and compact-prompt patch retain a float-store path that still fails observed cases. This is evidence that one useful targeted proposal was lost at the text-application boundary. It does not establish that patches generally outperform full regeneration; there was only one saved proposal per arm for this exposed function, and the original prompt did not contain the newly exposed counterexamples.

Original pilot responses and invalid statuses remain unchanged. Only the existing unique whitespace-relaxed matcher reapplied the two saved patch proposals, followed by the same function-boundary, signature, source-escape, and no-op checks. No semantic rewriting or new model generation was used in recovery.

- `report.json`: scores, verifier/frontend results, semantic feedback, allocator assembly, fixture derivation, and live-pin verification.
- `panel.json`: identical input panel and preserved environment/coverage limitations.
- `parent.c`, `patch.c`, `full_function.c`, `compact_patch.c`: evaluated sources.
- `attempts.sqlite`: isolated attempt receipts.

Run: `python -m eval.experiments.campaign-gap-audit.repair_output_recheck --allocator-state`.
The output directory is exclusive and refuses overwrite. Builds and database writes used a temporary isolated workspace. Live campaign pins were checked before and after and remained unchanged. No candidate was integrated into the live campaign.

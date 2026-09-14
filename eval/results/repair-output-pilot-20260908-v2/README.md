# OSS repair output pilot — September 8, 2026

Six fresh local GPT-OSS calls: two development functions, three output modes, one call per mode/function. Every mode started from the same saved parent with the same per-function semantic panel and byte residual; no adaptive child selection or retries. Whole-function output replaced only the selected function, preserving surrounding source and its signature. The frozen game-wide campaign was not modified.

| Output mode | Applied, compiled, frontend accepted | Output tokens | New exact matches |
| --- | ---: | ---: | ---: |
| Targeted patch | 1/2 | 1,128 | 0 |
| Whole function | 2/2 | 2,260 | 0 |
| Compact-feedback patch | 0/2 | 2,468 | 0 |

These six calls used 5,856 output tokens. The compact prompts were slightly longer than ordinary patch prompts because there was little removable feedback and the omission metadata added overhead. This is not evidence about effective context compression.

## Candidate results

- `osCreatePiManager`: targeted patch raised weighted matching score from 70.485 to 70.737; whole-function output lowered it to 54.530. Both retained 30 passing / 34 failing semantic cases. The compact patch omitted an existing statement from its old-source anchor and was rejected.
- `alSynSetFXMix`: whole-function output lowered score from 53.442 to 52.455. Both patch proposals failed strict application because their indentation differed from the source. The fresh panel's 57 passing cases covered only 9/36 target instructions and 1/6 branch outcomes, skipping the edited body. They do not demonstrate behavioral correctness.

## Saved-response recovery (zero additional model calls)

The separate [recheck report](../repair-output-recheck-20260908/report.json) reapplied the two audio patches with the existing whitespace-tolerant applicator and then revalidated function scope, signature, and prohibited constructs. Both compiled and passed the frontend. Normal patch score rose to **74.389** (+20.947); compact patch fell to 51.044. Neither was byte-exact. These are weighted matching scores, not percentages of bytes correct.

Appending a historical failing input produced 57 passes and one inconclusive comparison for every candidate: current concrete `__allocParam` execution faulted on uninitialized allocator state. The old input had relied on an opaque allocator return. This identifies an environment/coverage blocker; it does not establish that the repair fixed the behavioral failure.

The recovered normal patch stores integer `fxmix`, while the other proposals retain a float store on at least one path. The two recovered anchors contain no string or character literals. Do not generalize this recovery to arbitrary source: the existing whitespace helper is not a full literal-aware token matcher.

## Interpretation and validation

### Concrete allocator follow-up

A [second no-model recheck](../repair-output-allocator-recheck-20260908/report.json) initialized allocator state and added four cases (`fxmix` 1, 127, 128, 255), retaining concrete callee execution. All four candidates used the same 61-case panel. Target instruction coverage increased from 25% to 94.44%.

| Candidate | Pass / fail | Weighted matching score |
| --- | ---: | ---: |
| Original parent | 57 / 4 | 53.442 |
| Recovered targeted patch | 61 / 0 | 74.389 |
| Whole-function response | 59 / 2 | 52.455 |
| Recovered compact patch | 59 / 2 | 51.044 |

All four compiled and passed the frontend; none was byte-exact. This establishes a behavioral improvement on the constructed development panel for the recovered targeted patch, not universal correctness. The earlier six-call pilot and inconclusive replay remain unchanged. Live campaign pins were again verified unchanged.

Targeted edits are worth retaining, with better source anchoring and coverage checks. Whole-function output avoided application errors and later fixed two of four constructed failing cases, while the recovered patch fixed all four. Two functions and one sample per mode cannot establish a general winner. No resume-ready game-wide accuracy figure follows from this pilot.

The context and output-validation tests pass: **17 tests**. Prompts, raw responses, parents, candidates, panel receipts, and the private attempts database accompany [report.json](report.json). Original rejected outcomes remain recorded separately from no-model recovery. All rechecks verified unchanged live campaign pins.

# Independent operand delivery review

Scope reviewed: the seven proposed deployed files (`eval/agentrepair.py`,
`eval/completion_campaign.py`, new `eval/operand_repair.py`,
`solver/regalloc_search.py`, `solver/regalloc_mutations.py`, new
`solver/local_web_merge.py`, and `solver/repair_queue.py`) against
`before-fix/`, plus the new focused tests, the frozen-overlay stage and
amendment scripts, and adjacent worker import/controller code. This is a review
of the scoped change, not of the unrelated dirty working tree. The proposal
generator is evaluated as a proposal source; ordinary frontend and object
certification remain the acceptance gates.

## Critical

None found in the reviewed scope.

## Important, corrected before final stage

1. The original `eval/operand_repair.py:115` logged each compiled child as
   `receipt`, but
   `eval/campaign_workers.py:9-20` remaps only `receipt_id` and other named ID
   keys when it merges a worker DB. Thus the canonical JSON receipt's log can
   retain private worker IDs while its top-level `attempt_id` and each
   `parent_attempt_id` have been remapped. The imported attempts and
   `attempt_edges` are correct, but the user-facing attempt log can point at
   an unrelated or absent durable attempt. The field is now `receipt_id`, and
   `test_worker_log_receipts_remap_after_other_worker_import` checks a nonidentity
   map. A direct
   `campaign_workers.remap` check with worker IDs 1/2 mapped to 100/101 left
   `receipt: 2` untouched while remapping `parent_attempt_id` and the top-level
   `attempt_id`.

2. The original `solver/repair_queue.py:232-238` hashed the new merge module but
   omitted its
   direct behavior dependencies `solver/c89.py` and
   `solver/repair_context.py` (`solver/local_web_merge.py:11,122-123`). A later
   change to masking or function-boundary parsing would alter generated
   candidates without changing `operand_repair@<revision>`, so the once-per-
   version visit would stay exhausted. Both files are now in the digest, with
   one change-detection test per helper.

3. `PIPELINE_MAP.md` initially omitted the newly scheduled operand worker,
   register-search evidence/receipt path, or local-web family. `AGENTS.md`
   explicitly requires the map to be updated when actual pipeline wiring
   changes. The September 26 retained-candidate section now describes the
   deployed path, evidence, gates, and pinned linker-map input.

## Minor

None requiring a code change found.

## Final evidence

The final seven-file stage manifest is SHA-256
`9c7d9b96e75f4548ceb2aedf849a884ca976524a3998f376d415a727c7d89056`
at checkpoint 28456. All seven staged file hashes equal both the manifest and
current main-tree source hashes, and the five previous files match their saved
before-images. The manifest records the old frozen hashes,
3,306 unchanged pins, the inventory/model identities, 972 existing exact or
integrated functions, and exactly one added linker-map pin. The copied actual
frozen package plus scoped overlays passed 190 tests with one skip in 10.71 s;
`stage-test.json` binds that test run to the manifest hash. Three dry amendment
tests passed, and `apply_amendment.py --validate` accepted exactly seven code
files and one new map pin. The stage/amendment
scripts require both pauses and locks, old hash checks, a consistent campaign
database, and backup/restore; the amendment has not been applied in this
review.

The register adapter now records normal and failed probe attempts in the
worker connection, associates each child with its compiled source parent, and
passes fresh attribution/frontend/recipe evidence into the mutation stream.
The operand route checks retained source against the durable attempt, caps
proposals at 72, logs the baseline and children, preserves an unchanged
incumbent/frontier/semantic receipt, and accepts a changed candidate only
after a passing frontend. The second two-node private copied-frozen
normal-controller canary, against the final staged project at checkpoint 28456,
completed two deterministic items, added 18
attempts and 18 parent edges, left no inflight work, and used no model
attempts. `audioThreadMain` reached a frontend-passing object-section
certificate after 13 proposals; `releaseSoundEffectHandleNode` reached the
same gate after three proposals, with the
`local_web_merge:temp_v1+temp_v1_2` child SHA-256
`6a1b329a5be400cc790296112c8757b097761fc1e642f68b4e9e9241fdd82af5`.
Both prior job histories were retained and the private DB had no foreign-key
failures. Fourteen audio worker attempt IDs shifted on import. The strict
`audio/controller_audit.py --report controller-canary-v2.json
--require-nonidentity --require-log-receipt-ids` audit passed: each canonical
log ID resolves to the imported attempt and its matching parent edge; accepted
IDs equal the sole exact receipts. This is a development case with
project-header assistance. I inspected the v2 report and audit source; the
audio agent ran that native audit successfully. My own WSL invocation returned
`E_ACCESSDENIED`, so I did not independently rerun its native DB checks.

The three Important items are corrected in the final staged files and tests.
No review blocker remains for the scoped amendment. The amendment and wider
campaign continuation are separate operational steps; this review does not
claim their outcomes.

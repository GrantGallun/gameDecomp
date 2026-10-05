# Operation: import re-verified candidates into the campaign, provenance kept

Owner, 2026-09-30: "whatever you think is correct long term", in reply to whether to import the kb-ledger candidates
with their tier kept. This is a node and ledger change, done under both pause markers with nothing in flight. It
follows `../20260930-jump-tables-plateau/`, whose certificate makes most of these candidates function-exact.

## What and why

The candidates are the jump-table re-score (`eval/results/loop-shape-20260930/rescore_fb.jsonl`) plus the exacts
from today's site-edit and plateau runs in trial databases. Their sources live in the kb ledger or in trial DBs, so
no campaign route could re-verify them or send them to integration.

Rules (from `import_candidates.py`, after `eval/cohort_reconcile.py`, "re-verify, do not believe"):
- Every candidate is compiled again through `workspace.score` against the campaign ledger. It is applied only if it
  is object-exact, or function-exact under the ROM-backed certificate.
- The strategy is `ledger-import:<root strategy>|via:<route>:<origin>`. `eval.status` keys tiers on this string, so
  a recovered root stays in the recovered tier, and a derived edit carries its root's tier.
- The node changes only through the controller's `completion_campaign.accept`, with profile
  `ledger-import@20260930`.
- Skipped: nodes already done, the sealed held-out 50, candidates already the node's source, and names outside the
  cohort.

## Dry run (`import-dry.json`, trial copy, state untouched)

9 → object_exact, 32 → function_exact_pending_integration, 1 → pending (frontend), 5 already object_exact in the
campaign (the live mined lane found them), 2 already the node source, 2 not in the cohort.

Most of the 32 pending-integration candidates are recovered tier: `authorized-target-history-recovery` or
`historical-provenance`. They count toward completing SBK1, not toward capability.

## Applied

Applied 2026-09-30 at checkpoint 36697, after pausing and draining (36696, nothing in flight). The result matches the
dry run: 9 nodes → object_exact (1,024 → 1,033), 32 → function_exact_pending_integration (21 → 53), 1 stays pending.
42 `ledger-import:` attempts are in campaign.sqlite, 9 exact. The pointer summary refreshes at the next controller
checkpoint. The pause markers were left set on purpose: another session (gamedecomp-75) is staging
`../20260930-object-layer/` while the campaign is drained, and it resumes the service afterwards.

# Independent review and corrections

Reviewer: `/root/review_transition_planner`; read-only code review and receipt
audit, without new compiler runs. Final review found no remaining material
validity defects within the reviewed modules and experiment.

Each concrete finding received a regression test before its fix:

- Checksums alone admitted invalid graph claims and out-of-range transition
  masses. Graph reload now reconstructs the original evidence; model reload
  validates conditional rows, with optional graph-bound fit recomputation.
- Nonexact certificates/frontend results could refer to unrelated sources or
  targets. Present bindings now validate on failures too, accounting for the
  recorded project macro wrapper.
- Two-step planning ignored remaining depth. Horizon now also respects the
  depth allowance.
- Compile-ordered action histories could masquerade as generator-ordered replay.
  Ordinary replay/merge reject action histories; proposal-aware replay regenerates
  the exact recorded decisions before revealing any receipt.
- JSON key reordering could break reload. Original worlds now iterate in
  canonical digest order.
- One receipt ID could claim several attempts in a world. Per-world receipt IDs
  must now be unique.

An additional implementation regression checks that previewing an exhausted
generator does not mark its stream observed/exhausted while actions remain pending.

The reviewer independently reran the audit without writing artifacts: 264 SQL
receipts, 238 parent edges, all planner decisions, model reconstruction and five
frozen module hashes passed. The reviewer also verified sorted-key JSON reload.

Both arms have seven exacts; the sole efficiency improvement is GetThreadPri
3→2 on a development case. All six model rows have one supporting target. No
nondevelopment case exercises model guidance, so predictive transfer is untested.
The complete final scoped suite passes 234 tests on each of Windows and WSL;
platform receipts identify the tested modules.

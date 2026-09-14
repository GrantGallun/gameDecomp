# Register-allocation search: campaign amendment `20260913-regalloc-search`

**What changed in the live campaign.** Revision `resume-pipeline-20260908/revisions/20260913-regalloc-search`,
applied at checkpoint 16197.

- **New profile.** `regalloc_search` is a zero-model profile with a budget of 300 compiles. It runs only for nodes
  whose largest residual fault class is register allocation (`solver.regalloc_search.register_dominant`), in the
  byte and environment lanes.
- **Inside the work item.** `agentrepair.run(regalloc_budget=...)` runs `solver.regalloc_search.search` over the
  tested generators in `solver.regalloc_mutations`, ranked by the `solver.regalloc_signature` gradient.
  - Search compiles go to an in-memory scratch attempt log.
  - Only a best candidate that improves on the source is re-scored into the campaign database, as one
    `agentrepair-regalloc-search` attempt. It then enters as an ordinary initial state.
- **Scheduling.** Register-dominant nodes with at most 2 other faults are scheduled ahead of their visit band
  (`repair_queue.project`). Nodes with more other faults keep their normal band.
- **Unchanged.** Acceptance, byte certificates, the ratchet, integration, model and GPU budgets, and every other pin.
  Nothing was imported: every match comes from a campaign work item accepted by the existing oracle.

**Evidence before deploying.**
- The offline result (`eval/results/regalloc-20260913/README.md`): the search reached 73 of 78 register-only
  functions unaided, and 63 of 145 with at most two other faults using a frozen generator set.
- The staged full suite passed 2,608 tests (`staged-tests.log`), including new wiring tests
  (`test_regalloc_campaign.py`) and fire tests for every generator.
- The end-to-end hook fire test (`validate_hook.py` → `validate-hook.json`) ran the staged `agentrepair.run` on 3
  real functions. All 3 came out object-exact in 6–12 compiles, with exactly 1 logged attempt each.

**Procedure.**
1. `stage.py`: exact-anchor edits over a copy of the live frozen code, recorded in `staged-manifest.json`.
2. The staged suite.
3. The campaign was paused with `campaign_service pause` and drained (`drain_status.py`).
4. `deploy.py`: the prior amendments' protocol. It archives the checkpoint pointer and old code, verifies every
   unchanged pin, requires the changed pins to be exactly the runtime files in the manifest, and asserts node
   statuses and exact counts are unchanged by the deploy. Output: `deployment.json`.
5. `campaign_service resume`.

**Rollback.** While paused, restore `revisions/20260913-regalloc-search/previous-code/*` over the live code, together
with the archived pointer in `previous-state-store.json`.

**Live check.** `live_status.py` counts completed `regalloc_search` jobs and how many of those nodes are exact.

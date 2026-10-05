# Mutation-selection wiring, September 28

Implemented opt-in `mutation_count`, `evolvability`, `mutation_count_diverse`,
and `evolvability_diverse` arms in the existing register-search engine. Production
defaults remain gradient selection. No live campaign amendment, native experiment,
triage compile, KB mutation or training export was performed.

Configuration, selection ordering, budget model, limitations, paper references,
and freezing/running instructions are in
[the suite README](../../research_suite/README.md#mutation-opportunities-and-evolvability).
Fresh bundles are required because participating implementation hashes changed.

## Verification

Final focused verification: **182 passed** in 2.38 seconds, Python 3.14.4 on Windows.
Receipt: [mutation-selection-focused-tests.xml](mutation-selection-focused-tests.xml).

```powershell
python -m pytest tests/test_regalloc_evolvability.py tests/test_regalloc_search.py tests/test_regalloc_signature.py tests/test_regalloc_mutations.py tests/test_regalloc_attempt_logging.py tests/test_regalloc_campaign.py tests/test_callback_joint_search.py tests/test_research_search.py tests/test_research_counterexamples.py tests/test_research_proposals.py tests/test_research_suite.py tests/test_research_runner.py tests/test_research_cli.py -q --tb=short
```

Synthetic controls cover worse intermediates versus better dead ends, retaining
the best result separately, sampled quality versus raw branching count, source
identity and own attribution after keyed reuse, enabling roots, failed probes,
probe-result reuse, depth/budget limits, conditional selection probabilities,
unknown preview tails, actual access to previewed moves, and adapter wiring.
These establish mechanisms; they do not establish IDO yield improvement.

The wider preceding run additionally included `tests/test_controller_optimizations.py`:
**184 passed, 2 skipped, 1 failed**. Receipt:
[mutation-selection-tests.xml](mutation-selection-tests.xml).
The failure, reproduced in isolation, was
`test_sync_preserves_all_run_configs_including_unattached_history`:
`eval/campaign_workers.py:42` raises `PermissionError [WinError 32]` while renaming
`private.initial.sqlite`. That unchanged implementation retains the SQLite
connection through the rename; SQLite's connection context manager ends the
transaction without closing the connection. This unrelated Windows failure was
not changed or hidden by marking the test as skipped.

Read-only review identified capped previews being mistaken for exhaustion and
preview bounds exceeding actual expansion bounds. Both received failing controls
before fixes. Shared-key regression tests also exposed and fixed the omitted
compile charge when an audit raises before a restart, and expansion after a
candidate's own recompile failed. Existing default-policy regressions pass with
those accounting/evidence fixes.

Native IDO integration and paired development-cohort trials remain deferred until
the competing pilot finishes. No new match count or performance gain is claimed.

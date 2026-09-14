# Zero-gain repair investigation

The immutable checkpoint 13035 audit covers the latest 300 saved work items
over approximately 60.5 minutes. In that sample, local_rewrites ran 164 times,
reported two byte-score improvements, but changed the selected source 161 times.
schema_patch ran 70 times, with four score improvements and 65 source changes.
Across the sample, 143 zero-byte-gain items changed only include directives
(101 local_rewrites, 31 schema_patch, five semantic_alternative and six
semantic_counterexample). This is a source-diff classification, not a claim
that byte score alone measures all useful progress.
Source-bound receipt hashes and reproducible audit code are retained in this
directory. Zero byte gain alone does not rule out semantic or frontend progress.

Inspection found a concrete selection bug: initialization selected the first
exploration frontier entry, whose semantic tie ordering could replace the
incumbent with a retained, equally rated alternative. Actual examples only
removed or reordered includes. This changes the source/evidence identity and
reopens strategy budgets without measured progress. See `source-diffs.json`.

The fix keeps the freshly evaluated root on a measured tie and compares all
initial candidates for strict improvement. Exploration alternatives remain
available. Real semantic improvement can still win with equal or lower byte
score; compiler/frontend fixes and strict byte gains also remain eligible.
No extra model budget, promotion rule, or historical evidence mutation is added.

Seven targeted selection tests include two regressions that fail before the
fix. The combined focused selection/modelrepair/agentrepair suite passed 42
tests. Frozen suite and real paired replay results are recorded separately.

The frozen suite passed 2,478 tests in 49.49 seconds. A paired replay of
`initCoursePreviewCloseSparkles` used the same source/retained alternative,
32-candidate deterministic budget, zero model calls and 64 synthetic cases.
Old selection changed the source at score 84.226; patched selection preserved
the source at the same score. Both results remain nonexact. Separate fresh
private databases contain logged attempts; native WSL workspaces hold builds.
See `replay-frozen-1789315614686390316/` and
`replay-staged-1789315639752085331/`. This is evidence of stopping source churn,
not of a new exact match.
Applying the actual campaign acceptance and scheduling functions to the two
replay results demonstrates the operational effect: old selection changes the
evidence key and repeats `local_rewrites`; patched selection preserves the key
and advances to `schema_patch`. Both executions passed all 64 finite cases.
See `replay-queue-projection.json`.

Capability scope: `status-readonly.log` records the unchanged status CLI's
read-only snapshot: 608 SOLVED and 109 header-assisted exact functions, zero
recovered from target source, including 15 matches present only on disk. These
are broader database/filesystem counts, not the campaign cohort count. Its
test-collection count is not a test result.

This correction removes demonstrated wasted retries. It does not establish a
higher long-term matching rate or solve the remaining large-function residuals.

Installed frozen revision `20260913-incumbent-selection` at checkpoint 13086,
after draining to 13085. Only the modelrepair runtime pin changed; all 706
campaign exact/integrated functions were preserved. Normal batch-10 resume was
issued with unchanged worker/model budgets. See `deployment.json` and the
post-resume `live-validation.json` for the latest confirmed progress.
Validation at checkpoint 13107 confirmed six new completed work items,
706 exact/integrated functions retained, matching release files and a running
service with a fresh heartbeat.

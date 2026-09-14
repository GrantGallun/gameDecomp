# Semantic evidence and scheduler follow-up — September 6, 2026

## Implemented

- ELF reader loads initialized allocated non-code section-symbol bytes as well
  as named objects. Relocated sections decline; payloads remain bounded to 256
  bytes. Candidate format strings no longer appear as unknown `.rodata` pointers.
- Causal call feedback uses normalized argument equality, matching the actual
  gate: equal format contents at different addresses are not a repair objective.
  Output-buffer addresses remain compared, not silently ignored.
- Stack-buffer hypotheses accept repeated calls only when every static occurrence
  agrees on the same source local/target slot. Address-only byte-pointer casts
  are supported; ambiguous binding and scalar-value uses still decline.
- A closed 12-instruction word-pair multiplication dialect executes actual
  64-bit intermediates. The entire stream must match, then reassemble to ROM
  bytes (`.set gp=64`). Both v0/v1 result words survive at the o32 caller boundary.
  No function-name dispatch or general 64-bit backend is claimed.
- Recognized header integer pairs consume two argument words and return v0/v1.
  Sparse alignment padding remains explicit debt, not a guessed contract.
- Nested-call feedback includes a bounded suffix exposing result construction.
  A later source-contract feed identifies explicit one-word return declarations
  contradicting this binary interface. It binds source lines and assembly hashes;
  it does not silently rewrite C or claim complete prototype compatibility.

## Zero-model paired replays

Receipts: `eval/results/semantic-gaps-paired-v1.json` and `-v2.json`. v2 includes
corrected two-word square-root arguments. All sources were freshly compiled and
logged with actual parents. No official C bodies, integration or promotion.

Save panel: section bytes remove the false format-pointer mismatch. Generated
`sp6C[28]` (attempt 30383) advances the first case's matching call prefix from 6
to 11. Both root/child still fail 64/64 complete comparisons, weighted score
86.988, nonexact. Next mismatch: target second-iteration loads player+0x1a/+0x22,
candidate loads player+0x68/+0x70. Target advances by 2 bytes; `var_s0 += 2`
advances two 0x28-byte structures. Formatting callees remain opaque: no claim
that actual formatted-output effects are validated. Buffer extent is a hypothesis.

Homing target: current concrete-helper/two-word-argument panel passes 58/64,
fails six. Disabling concrete multiplication on these same cases gives 58 pass,
one failure and five inconclusive executions. Twelve actual helper calls execute
on the target across this panel. Its candidate `s32` declaration loses v1 and
corrupts the downstream sum. Prior 57/64 used a different ABI/environment/panel;
the changed counts are NOT a candidate improvement metric.

## Frozen campaign and model follow-up

`autonomy-progress-8-v3.json` explicitly forks v2 using `semantic-gaps-code-v1`.
Same eight functions, 24 visits, eight model calls, 9,159 recorded generated
tokens. No mid-run edits, pin failure, crash, integration or new exact match.
Budget paused, not strategy-exhausted. Final stages: two compiler/frontend
passes with observed semantic failures, one frontend-rejected compiler pass,
four noncompiling, one hardware-backend blocker.

Both `__osViInit` and `drawRaceIntroFlyoverActor` now reach `schema_patch` after
two recovery visits. The actor previously received three recoveries and no
model visit in v2. This live-tests the recovery-churn cap. Calls-per-visit also
changed, so this is not a controlled model-performance A/B.

After v3 stopped, added the explicit source-contract feed and froze
`semantic-gaps-code-v2`. Receipt `semantic-gap-contract-homing-v1.json`: one
call, 2,497 tokens, no improvement. OSS left the `s32` declaration intact, tried
shifting an already truncated value, and emitted a no-op second edit. Proposal
rejected; no compiling child. Verified root remains 58/64, score 77.540, nonexact.
This is one negative repair trial, not proof that OSS cannot solve the function.

## Validation and next work

Final Windows suite: 1,221 passed, 12 skipped. Frozen v1 WSL targeted suite:
49 passed, including ROM-bound helper admission. Fixtures cover overflow/both
result halves, unsupported streams, ambiguous buffer binding, altered strings,
and candidate ABI conflicts. All workers stopped; old receipts preserved.

Next: coordinated result-representation repair (declaration, locals, high/low
extraction, final sum), not another carry-only edit. For the save panel, map
array/view iteration onto existing header fields while retaining the buffer fix.
Actual formatting/output side effects remain separate environment work. Do not
silence pointer differences or claim full semantics from opaque calls.

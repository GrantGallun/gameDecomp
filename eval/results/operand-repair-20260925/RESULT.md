# Result: operand repair with full evidence: 20 new exacts, a new owner, and a certificate soundness fix

Protocol: `PROTOCOL.md` (written before the run). Pool: the 130 structurally-correct campaign functions
(`alloc-census-20260924`). Scoring through the main-tree `workspace.score` (attribution, frontend, certificate) in
isolated repos, logging into a private DB.

## Wiring gap found first (the silent-decline rule)
`evidence_site` needs the verdict's `source_attribution`; the copied-workspace harness used for the binary-types
searches never computed it, so that family never fired there. `diffrepair` was not in the stream. With the official
scorer, evidence_site fired 22 times on this pool.

## The rodata-literal class, and what it hid
The pool's "operand" residuals included 13 functions whose only non-register difference was a constant read
through a named symbol or a section (`%lo(D_800E0A50)` / `.late_rodata`) where the candidate read its own `.rodata`.
- New owner `solver/rodata_symbol.py` (tests: `tests/test_rodata_symbol.py`): on the compiler-attributed line, write the
  constant with the value from binary evidence (ROM bytes at the named symbol, or the target object's own
  `.late_rodata`), as a literal (shortest exact decimal) or as an `extern` read of the named symbol.
- **Its motivating case was a real bug.** initControllerPakFileDeleteFlow's draft passed `-1.33333333f`; the ROM holds
  `+4/3`. The object comparison could not see it: the constant lives in rodata, which the certificate never read.
- **Certificate stage 3** (`solver/byte_certificate.py`: `progbits_contents`, `rodata_value_relocations`,
  `rodata_equivalent`; receipts carry `rodata_values_compared`): section-relative `.rodata`/`.late_rodata` references
  compare by the BYTES they read (width from the load opcode, address from the REL addend), and a candidate-only
  allocated `.rodata` is accepted only when its contents equal the target's `.late_rodata` byte for byte. This both
  admits correct constants across the two section names and rejects the sign-flipped one (tests in
  `tests/test_byte_certificate.py`).
- Soundness of existing exacts: before this stage, a certified exact either referenced the same named symbol as the
  target (value identical by linking) or could not be certified at all (candidate `.rodata` against target
  `.late_rodata` never matched). The hole was in how "score 100, not exact" was READ, not in anything counted exact.

## Outcome on the 130 (`summary.json`, recorded in `receipts.json`)
| | functions |
|---|---|
| exact after search | 17 |
| exact at baseline under the new certificate (the campaign's own source) | 3 |
| improved | 12 |
| flat | 98 |
All 20 confirmed through the official scorer (certificate plus frontend gate) and recorded in the KB (receipts
96671-96691), tier `project-header-assisted` (the campaign's sources include game headers). **All 20 are new to the
project** (campaign-unmatched). Families on exact paths: typed_reread 12, index_form 2, then one each of rodata_symbol,
at_inline, do_restore, local_type, stmt_move, compound_assign, single_use, field_local, owner:drop_mask. By census
class: split 6, ugen_temp 6, none 6 (3 at baseline), blocked 1, declined 1: the full-evidence stream closed allocator
cases the campaign's frozen snapshot had not.

## Frontier re-certification and the next band (same day)
- `recert_frontier.py`: the other 883 compiled-not-exact campaign best sources, one compile each under the current
  certificate: 6 exact at baseline (relocation-pairing and rodata-value cases), recorded (`receipts-recert.json`; 5
  header-assisted, 1 source-independent), all new to the project.
- Amendment A1, band 1-2 (129 functions with 1-2 structural steps): 6 exact, 42 improved, 81 flat; recorded (5
  header-assisted, 1 source-independent), all new to the project. Families on exact paths: evidence_site 2,
  residual_evidence 2, field_local 2, local_type, stmt_move, at_inline, frontend_type. The wiring fix pays:
  evidence_site closes functions once it is given attribution.
- Also recorded from binary-types search round 3: osViSetEvent (source-independent); drawRaceUiBoardReversePrompt did
  not confirm (the harness had masked a real difference).
Running total for 2026-09-25: 20 + 6 + 6 + 1 = **33 new to the project**.
Full WSL suite after the certificate changes: 15 failed, 41 errors, 4,674 passed: the same failure and error counts as
the 2026-09-22 recorded baseline, with no failures in the changed modules.

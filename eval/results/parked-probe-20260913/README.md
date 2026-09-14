# What current main-tree code does with the parked functions

2026-09-13. `probe.py` replays the campaign's own `_intake` with **main-tree** code
on a copied database (`~/decomp/kb-sbk1-parkedprobe-20260913.sqlite`). The live
campaign and its frozen code were not touched. No model calls.

## Why this was needed

Parking is terminal: `completion_campaign.next_profile` returns `None` for a
parked node, so a function parked by a pipeline gap stays parked after the gap
is fixed. The live campaign also runs frozen code: its
`code/solver/compiler_recipe.py` dates from 09-07, while the main tree admitted
the `-mips3 -32` recipe on 09-08 at 18:37 -- about two hours after the long-long
helpers were parked for lacking it.

## The 62 parked functions

**27 are assembly by design** (blocker `hardware_backend_required` or
`sdk_control_flow_or_relocation_unsupported`): libultra CPU/kernel internals,
hand-written SDK assembly, and two RSP microcode blobs (`aspMainTextStart`,
`rspbootTextStart`). They stay assembly in a green build, as in the reference
project. Excluded from the probe.

**35 are ordinary C parked by pipeline gaps.** Probe results:

| gap | n | now |
|---|---|---|
| no `-mips3 -32` recipe (frozen code) | 10 | **8 object-exact** (oracle, `exact=1` in the DB), 2 not compiling |
| intake: unbalanced function body | 7 | no longer parked: 3 compile (91.3, 82.6, 64.9), 4 not |
| intake: requires one function definition | 6 | no longer parked: 3 compile (91.8, 86.4, 65.3), 3 not |
| out of memory 2026-09-09 (WSL then 2 GB) | 1 | compiles, 45.3 |
| object post-processing backend | 6 | still blocked: backend not built |
| target symbol resolution | 5 | still blocked |

The eight exact: `__ll_div`, `__ll_lshift`, `__ll_mul`, `__ll_rem`,
`__ll_rshift`, `__ull_div`, `__ull_rem`, `__ull_rshift`. All came from the
existing deterministic `campaign-compile-recovery:closed-wide-runtime` stage
(64-bit parameter recovery), e.g.
`s64 __ll_div(s64 wide_lhs, s64 wide_rhs) { return wide_lhs / wide_rhs; }`.
No exact body appears verbatim in the reference project's C.

**Not yet established:** the campaign's object-section certificate and the
whole-ROM integration gate were not run here. These are oracle-exact replays,
not campaign promotions.

## To realise this in the campaign

1. Deploy main-tree `solver/compiler_recipe.py` (and the intake fixes behind the
   13 no-longer-parked functions) through the amendment protocol.
2. Reopen parked nodes whose blocker is NOT assembly-by-design, so they re-enter
   intake. Nothing does this today; parking has no expiry and no re-check when the
   code that parked a function changes. A durable fix is to record the code/pin
   identity with the blocker and re-queue on change.
3. Remaining work: the six-function race-setup post-processing backend (one
   backend, one translation unit) and five target-resolution failures.

Receipts: `receipts/*.json`; full records: `results.json`; log: `run.log`.

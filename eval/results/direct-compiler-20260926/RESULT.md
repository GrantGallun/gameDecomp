# Direct compiler evidence: confirmed branch-default repair

`drawControllerPakFileDeleteConfirmOptions` went from **99.889 to byte-exact**.
The ordinary frontend and object/relocation certificate passed, and an
independent ordinary recompile confirmed it. The new deterministic rewrite is
wired into the main mutation stream. An isolated normal search starting from
the original candidate, with no winning child in its input database, reproduced
the exact result in **2 compiler calls including baseline, 0.994 seconds**.

The raw campaign and research exact ledgers both had zero prior exact rows for
this function when checked. This is a new result against those raw ledgers,
with inherited header/source assistance. It is not a clean source-independent
evaluation, a whole-ROM integration result, or proof of broad transfer. No
private candidate or new code pin was installed into the active frozen campaign.

## What the compiler directly showed

The frozen parent is native receipt 157771, source SHA-256
`24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72`.
Only instruction indices 10 and 12 differ in its 90-instruction normalized
stream: two `0x80` loads are exchanged around a conditional branch's delay slot.
All later register uses match. Earlier trace diagnosis called this `split`,
but that label describes mixed positional register votes, not an observed
target-compiler range split.

The actual `cc -S` output shows the parent emits `$3 = 128`, then `$8 = 128`,
before the inner branch. The previously failed assignment swap emits that same
physical-register order but exchanges which C local owns each register. It
therefore changes later branch values and spill bindings, without fixing the
initial order. These are observed compiler outputs, not predicted effects.

The successful C exposes both values in each arm:

```c
if (gControllerPakMenuState.confirmChoice == 0) {
    var_v1 = 0x100;
    var_t0 = 0x80;
} else {
    var_v1 = 0x80;
    var_t0 = 0x100;
}
```

Its pre-as1 assembly retains these branch definitions and the original register
roles. Its final assembly moves the defaults before/into the branch and has
exactly the target's order. Relative to the original parent, only final
instruction indices 10 and 12 change. We observed this phase transformation;
we did not reconstruct the target's unavailable internal trace or establish
the assembler's complete scheduling heuristic.

Direct compiler flags came from the recorded translation-unit recipe. The
asm-processor wrapper initially rejected two diagnostic `-S` commands because
it requires an object output. Those failures are retained in `phase-report.json`.
The corrected commands use the underlying compiler with the exact recipe flags
on these GLOBAL_ASM-free sources. Separate direct `-c` gates reproduce each
ordinary baseline, failed-swap, and winning object exactly. The `-S` files are
diagnostic evidence; ordinary scoring supplies the target acceptance certificate.

## Experiment and delivery receipts

- `manifest.json` and `manifest.sha256`: twelve source interventions frozen
  before observing results; candidate sources only, no reference or winning C.
- `report.json`: 13 ordinary attempts including baseline, all compiled and
  passed the frontend. One exact proposal, three assembly-identical to baseline,
  and eight worse scores. Entire isolated scoring stage: **4.745 seconds**.
- `audit.json`: all 13 sources, receipts and actual parent edges checked;
  raw-ledger novelty metadata; the previous evidence-gated stream emitted four
  proposals and did not include the winning source.
- `confirmation.json`: independent exact receipt 157785 in a fresh private
  workspace/database. Winning SHA-256
  `c6ae0473748e7e89e025dbcdf92f4ca6d9babbe6bb6b28dd5dce678e0bee9d13`.
- `baseline.pre-as1.s`, `prior_swap.pre-as1.s`, `winning-phase/`: direct phase
  evidence, command/source hashes, per-invocation timings, and reproduction gates.
- `search-report.json`: normal register search (budget 300, beam 3, depth 4),
  original native ancestry only, **2 calls / 0.994 seconds**, both edges audited.
  The winning action is `branch_defaults:var_v1,var_t0@108`.

The three successful pre-as1 captures took 16–19 ms each on this function.
Those timings exclude ordinary frontend/object validation. This turn also ran
three direct-object reproduction compiles, one independent ordinary confirmation,
and the two-call search canary, in addition to the 13-call experiment. The two
wrapper failures never reached the compiler. Engineering, investigation, review,
and audit time are outside the quoted scoring/search times. No learned predictor
or model call participated.

## Reusable implementation and limits

`solver/branch_defaults.py` recognizes two scalar integer-literal defaults
followed by a simple conditional that overrides one local in each arm. It
distributes the appropriate values into both arms. `regalloc_mutations.variants`
includes this family directly; existing search and ordinary acceptance gates
remain responsible for compilation and exactness.

The generator declines known unsafe or ambiguous contexts: nonlocal, volatile,
shadowed or address-taken variables; local-dependent/calling conditions; supplied
condition macros; comments in the rewrite span; function-body directives; and
braceless parents, including `for` headers. Macros available only through included
headers are not resolved by this source-only generator. Its output is always a
proposal, never a semantic proof or automatic acceptance.

Versioned fixtures contain this generated candidate pair. Tests require the
generator to emit the exact motivating source and reach the normal stream, in
addition to decline cases. Fresh review caught and fixed scope and braceless-loop
guards. The focused rewrite, neighboring branch/mutation/search, and harness/audit
suite passed **89 tests**. No miner or KB implementation, game build source,
model weights, or frozen campaign code changed. All probe artifacts remain
training-ineligible. Broader transfer and frozen-campaign deployment are untested.

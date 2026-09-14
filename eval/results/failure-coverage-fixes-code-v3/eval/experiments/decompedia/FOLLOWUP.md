# Follow-up: verify the seven queued claims and graphics lead

September 5, 2026. This extends [the initial review](README.md), without
overwriting its receipts. It adds a CPU-only graphics packet audit and measured
scope for all seven queued items. No new complete function match is claimed.

## Corrections to the supplied assessment

- Prior exposure was broader than a bookmark list. `COLOR_POOL` is used by
  allocator diagnostics, liveness and exactness-gradient code. Its module also
  explicitly labels spill-cost assumptions. Existence of the pool is not proof
  that the whole allocator model or every controller is validated.
- `bulk-struct-copy` already exists in the catalog with corpus provenance.
  Copy recognition and the guide's source-reordering claim are related but
  different mechanisms; a literal keyword census missed that distinction.
- `spimdisasm 1.42.4`, `n64img 0.3.3`, and `pygfxd 1.0.5` are installed.
  Splat imports all three, including pygfxd in its static `gfx` segment handler.
  [Installed-code references](tool-usage.json) establish transitive integration;
  they do not establish prior CPU-store-to-packet recovery in this solver.
- The recorded exact pool is 208 at the audit snapshot, not 98. This includes
  assisted/recovered provenance and is not a capability count. The DB's 2,113
  `functions.state=matched` rows describe the reference corpus, not solver wins.
- [Chris Lewis's article](https://blog.chrislewis.au/decompiling-a-nintendo-64-game-in-84-days/)
  is a primary account of the SBK project. It remains useful context; it does
  not substitute for measuring a proposed compiler rule on our pinned build.

[Current-state snapshot](current-claims-v1/receipt.json) records installed
versions, stored candidate IDs and source hashes. Fresh source-bound replays are
in [queued-v1](queued-v1/receipt.json), attempts 29704–29713:

| Proposed target | Fresh finding |
|---|---|
| checkMainMenuSecretCode | Ordinary object score 99.957; diff is jump-table symbol versus `.rodata`. Existing `matched_recovered/checkMainMenuSecretCode.relocated.json` verifies relocated text and table data. This is not evidence of missing switch-default behavior. |
| updateCourseSelectCourseDescription | 99.64; besides table symbols, the current residual includes wrong field offsets, a different state constant and branch operand order. Calling it only a jump-table residual loses those actionable differences. |
| bootThreadMain | 97.222; missing nop in the unreachable tail after an infinite unconditional loop. There is no conditional branch to explain with the proposed branch-likely heuristic. |
| initControllerPakRaceRecordSaveFlow | 99.912; the diff is precisely f4 versus f0 for a load and argument store. No saved-float-register hoisting is present in that residual. |
| compressRaceRecordReplayData | 98.478; 3,958 recorded attempts at snapshot. Three isolated address-spelling variants scored 98.478, 98.333 and 98.478. None improved the frozen parent. |
| drawPulsingAssetTableSprite / drawMenuSpriteWithAlphaClipped | Reverified 0.874 / 0.624. These remain real poor candidates; packet recovery below addresses one part of their reconstruction. |

Stored relocated success is reported separately from the fresh ordinary object
check; no relocation gates were changed and no historical receipt was relabeled.

## Seven compiler claims, checked separately

Source: the [archived OoT IDO 5.3 guide](https://github.com/n64decomp/oot/blob/master/docs/guides/-O2%20decompilation%20(for%20IDO%205.3).md).
Each [paired probe](queued-v1/receipt.json) uses the resolved SBK1 `-O2 -mips1`
recipe. These are synthetic known-source compiler checks, followed by development
candidate replays; they are not a seven-technique heldout effectiveness study.
Source, object and disassembly snapshots are retained. The
[supplemental probes](supplemental-v2/receipt.json) distinguish a named const
object, an immediate-friendly literal and a literal requiring `.rodata`.

| Queue item | Observed result | What is justified |
|---|---|---|
| #3 comparison normalization | Signed `x > 7` and `x >= 8` have identical text. | Confirmed for this threshold/type; range/overflow guards are required before general rewrites. |
| #5 unrolling | Eight-element loops unroll by four with and without trailing `continue`; adding it changes the induction counter to byte units. | A source-shape lever is real; “continue disables unrolling” is not a universal rule. |
| #6 array address | Simple `&a[i]`/`a+i` loop is identical; two compression edits are inert and one regresses. | Context-sensitive hypothesis, not the demonstrated solution to the 3,958-attempt case. Broader loop representation remains open. |
| #7 struct copies | Four-int aggregate/member copies retain the access order but choose different registers. | Source form affects codegen; this probe does not confirm reordering specifically. Existing bulk-copy recognition is still relevant. |
| #8 switch default | Moving a breaking default between first/last positions changes placement and adds a branch in a three-case branch-chain switch. | Default order matters in this probe. No claim that all defaults hoist or that jump-table placement is solved. |
| #9 literal hoisting | Named extern const reloads each iteration. `1.234567f` is loaded once from `.rodata` into f20 and copied into the argument register on each iteration. | Confirmed mechanism; named non-extern const storage did not reproduce it. It does not explain the cited f4/f0 residual by itself. |
| #10 branch-likely cause | Conditional-load source forms differ; conditional-store probes use ordinary branches under both mips1 and diagnostic mips2. | Cause remains unconfirmed. The original guide marks the explanation tentative; bootThreadMain is an inapplicable target. |

Six narrow observations now have review entries with receipts in the catalog.
The branch-likely cause remains a hypothesis with no `confirmed_on` evidence.
None has an automatic hint detector: compiler behavior, detection of its cause
in a target, and a safe source generator are three separate validation steps.
The confirmed literal result is not permission to replace external objects by
constants or alter ABI declarations without target evidence.

An additional live-format bug surfaced: `target_frame_size` parsed `-0x20` as
zero because its regex accepted only decimal digits. It now accepts hexadecimal
and decimal immediates; regressions use bootThreadMain's 32-byte spelling and
the graphics function's 168-byte spelling.

## Graphics result: a concrete CPU-only tool

[libgfxd](https://github.com/glankk/libgfxd) accepts Gfx command packets and emits
macro representations for a chosen microcode. The new adapter fills part of the
gap between CPU instructions and that input. It reuses the existing CFG and
must-dataflow analysis, identifies word pairs through the explicit
`gRegionAllocPtr` symbol, and handles literal expressions on a copy checked
against the annotated instruction words. It avoids cross-block/pointer-epoch
pairs and preserves dynamic words as unknown.

[Graphics receipt](graphics-v2/receipt.json):

| Function | Candidate pairs | Constant pairs | Verified macro instances |
|---|---:|---:|---:|
| drawPulsingAssetTableSprite | 20 | 10 | 9 |
| drawMenuSpriteWithAlphaClipped | 19 | 9 | 9 |
| Total | 39 | 19 | 18 |

The 18 instances include pipe/load/tile synchronization, combine mode, tile
setup and palette loading. They are repeated instances, not 18 distinct macro
kinds. Each emitted static macro was compiled with the target GBI header and
`F3DEX_GBI`; its eight bytes matched the target-derived pair, with only zero
section padding and no data relocations. One constant packet decoded as raw
words; 20 pairs contain dynamic values. Neither category was fabricated into
macro arguments. This is packet-representation validation, not CPU instruction
matching, runtime coverage or recovery of either complete graphics function.

Use the tool in WSL:

```bash
/home/grant/decomp/sbk1/.venv/bin/python -m tools.gfx_packet_audit \
  /home/grant/decomp/sbk1/nonmatchings/drawPulsingAssetTableSprite/target.s \
  --function drawPulsingAssetTableSprite --pointer-symbol gRegionAllocPtr \
  --microcode f3dex --out /tmp/pulsing-gfx-packets.json
```

The CLI was smoke-tested. It is an explicit audit tool, not automatic campaign
source replacement. The next useful graphics step is connecting the unresolved
word expressions to source-bound dynamic macro arguments and checking the
resulting CPU candidate with the existing oracle. No GPU is needed for the
packet extraction, decoding or compiler checks done here.

No model calls, heldout source, new model training, or game-source promotions
were used. Public compiler knowledge is useful shared infrastructure, but this
audit does not establish a blanket reuse status for everything linked by a wiki.

Validation: 79 focused tests passed across `test_gfx_packets`, `test_units`,
`test_dataflow`, `test_cfg`, `test_mips_likely_branches` and `test_m2c_input`.
The graphics CLI smoke test and the 18 compiler packet round-trips are additional
live-tool checks. `git diff --check` passed.

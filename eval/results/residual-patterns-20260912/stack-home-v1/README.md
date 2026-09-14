# Same-frame stack-home repair

The checkpoint audit contains 23 functions with a one-instruction signature of
target `lw R0,0x18(sp)` versus candidate `lw R0,0x1c(sp)`. That signature alone
does not establish a shared cause. Inspecting source-bound candidates identified
a narrow family of ending callbacks whose *entire* residual consists of one
matching word-store/word-load home difference. Their frame sizes, operations and
registers already match.

The existing `frame_padding_rewrites` only proposes padding when the target frame
is larger. `statement_order_rewrites` does not fire for this pure slot displacement,
and `code_shapes` yields no applicable shape for the selected simple callbacks.
An existing register-web variant compiled to the same incorrect slot.

## Experiments

All attempts use a fresh complete private copy of the existing popup-v5 attempt
history at `/home/grant/decomp/residual-stack-home-v1-20260912/history.sqlite`.
Selected sources are verified against the audit source hash before compilation.
The game source and live attempt database were not modified or copied. No model
calls or reference implementation bodies were used.

Three related callbacks were tested with the original candidate, the existing
register-local variant, a four-byte unused volatile array before/after the local,
and an eight-byte-aligned union containing the original word local. Every compiler
outcome is retained in the full private attempt history and `report.json`.

| Shape | Object-exact functions | Result |
|---|---:|---|
| Original scalar | 0/3 | 99.524 similarity |
| Existing register qualifier | 0/3 | Same result as original |
| Leading four-byte array | 3/3 | Compiler/frontend exact; 64-case observed pass |
| Trailing four-byte array | 0/3 | Same result as original |
| Aligned union local | 3/3 | Compiler/frontend exact; 64-case observed pass |

The final generic `rewrites.stack_home_padding_rewrites`, through ordinary
`rewrites.propose`, was then independently compiled and differentially evaluated:

| Function | Original attempt | Final generator attempt | Result |
|---|---:|---:|---|
| startEndingSlashRepeatAnim | 36935 | 44885 | Object exact, frontend passed, observed pass |
| updateEndingJamPhase3DPrep | 36944 | 44886 | Object exact, frontend passed, observed pass |
| updateEndingJamPhase3FAnim3 | 36953 | 44887 | Object exact, frontend passed, observed pass |

See `final-generator.json` for full sources, object paths, semantic reports and
all 23 applicability decisions. The final generator proposes one candidate for
7/23 source-bound functions and declines the other 16. All seven are ending
callbacks; this establishes a useful repair within one family, not broad transfer
across arbitrary functions with stack differences. No private pilot attempts were
imported into the campaign.

## Generator bounds

The generator requires every changed instruction to be an unambiguously aligned
`lw`/`sw` pair with matching registers and stack base; one shared target home must
be eight-byte aligned, and the candidate home exactly four bytes later. Both a
load and store must occur. Mixed frame, instruction, register, or multiple-home
residuals decline. Supported source has one uninitialized 32-bit integer local,
one function definition, no explicit address-taking of the local (including
parenthesized forms), and no existing generated pad name. Macro expansion is not
analyzed. The only emitted candidate adds one leading four-byte unused array;
all ordinary compiler/frontend/semantic/exactness gates remain required.

Catalog provenance is `single-local-stack-home-padding` (renamed from the initial
`ido-` prefix, reserved for unconfirmed imported entries; historical receipts
retain their original identifier). The final dedicated
and existing rewrite suites pass **102 tests**, including real objdump spacing,
normal proposer wiring, source binding, ambiguous alignment, mixed residuals,
multiple declarations, name collisions and explicit address-taking rejection.

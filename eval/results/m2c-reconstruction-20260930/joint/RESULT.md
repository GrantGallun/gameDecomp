# Native m2c multi-function DEV trial

No gain on the frozen sprite cluster. Native multi-function inference with two or four passes emits the same per-function drafts as two-pass individual inference under the identical shared binary-derived header. All 12 generated candidates were compiled and recorded in a private SQLite database, including all six compiler failures.

| Arm | m2c invocations | Isolated compile attempts | Compiler + frontend passing functions | Byte-exact functions |
| --- | ---: | ---: | ---: | ---: |
| Individual, two passes | 4 | 4 | 2/4 | 0/4 |
| All four together, two passes | 1 | 4 | 2/4 | 0/4 |
| All four together, four passes | 1 | 4 | 2/4 | 0/4 |

The passing functions are `drawMenuSprite` and `drawMenuSpriteWithAlpha`. `drawMenuSpriteClipped` and `drawMenuSpriteWithAlphaClipped` fail IDO compilation on the same unresolved `void *` additions and indexed pointer stores in all arms. There is no acceptance certificate for those failures. The wrappers have compiler/frontend receipts but their object sections differ from the binary-derived targets.

## Actual inference treatment

This trial supplied four normalized assembly files to one native m2c invocation, rather than independently translating four functions and giving them a common header. Installed m2c commit `ef8aff103d1eb6fcc4fb73be2d1f887439bcb0cb` constructs one `TypePool` and one `GlobalInfo`, builds all four decompilation states, translates every state through its preliminary passes, prunes shared structs, then emits the final pass. A copy of the installed implementation and its package hashes is retained for inspection.

## Frozen controls and output comparison

The selected functions and 12-compile budget were frozen in `portable/preregistration.json` before generation. These are the same four exposed DEV functions as the previous joint-header spike, with no held-out overlap. Every invocation used the same preprocessed context and `--target mips-ido-c --no-cache --valid-syntax`; only the input file list and the separately labeled four-pass sensitivity arm changed. Input assemblies contain each function body once, excluding repeated assembler macro/register preludes. Original target objects were copied without alteration from the earlier binary-only trial, and their hashes were audited.

All four raw function bodies, all four lowered candidate sources, and the grouped two-pass versus four-pass stdout are identical. The saved per-function body diffs are empty. The unchanged `m2c_byte_view.lower` was applied separately to each extracted body in every arm; no byte cursor treatment was added. Each compiled isolated candidate removes only its own preliminary prototype from the same header, keeping callee declarations. Context, tool/header/build recipe identities, stdout/stderr, source, diagnostics, diffs, certificates, frontend receipts, and all 12 attempt records are retained under `portable/`.

`verify.py` audits the 12 private attempt records, target/source digests, receipt outcomes, and equality of drafts across arms. Builds and receipts reside natively at `/home/grant/decomp/experiments/m2c-joint-native-20260930-v1`, with a complete portable copy here.

## Limits

The shared header already supplies detailed binary-derived type and function hypotheses, which may leave little useful additional inference for this cluster. This result does not show that native joint inference is useless with weaker contexts or other clusters. No reference C bodies were read, no model was called, no production KB or generator was changed, and no global match ratchet was measured. These are isolated function compiles; the combined generated translation unit and final ROM were not checked. Compiler/frontend success does not establish full-domain runtime equivalence.

## Exploratory secondary: public prelude only

After exposing the primary result, a separately preregistered secondary trial removed all prior sprite layouts, globals and prototypes from m2c's input context, keeping only the clean public SDK prelude. It ran the same four DEV functions alone versus grouped with two passes, using eight additional compile attempts. Total native compile attempts: 20; total private attempt records: 20 across the primary and secondary databases.

| Secondary arm | Isolated compile attempts | Compiler + frontend passing functions | Byte-exact functions |
| --- | ---: | ---: | ---: |
| Individual, public prelude, two passes | 4 | 0/4 | 0/4 |
| All four together, public prelude, two passes | 4 | 0/4 | 0/4 |

All four function bodies and function definition signatures remain identical between the secondary arms. The emitted callee declarations do change: an individual wrapper emits an unknown `M2C_UNK` return and an 11-parameter `drawMenuSpriteClipped` declaration; the grouped invocation emits a `void` return and a 12-parameter declaration. Its consumer definition has 12 parameters in either arm. The alpha pair shows the analogous 12-to-13 declaration refinement. Native shared inference is therefore observable at the declaration level even though it does not improve the function bodies here.

Both wrapper definitions still omit the incoming pass-through `arg2` parameter and their calls still omit that argument. These drafts and signatures are unverified hypotheses. Grouped stdout also emits call-derived scalar parameter declarations that conflict with its generated pointer/width-specific consumer definitions. The isolated grouped compiler candidates retained the emitted types/global declarations and generated helper signatures, so these conflicting prototypes remain an additional header-validity limitation.

The actual compiler failures occur first on unresolved `M2C_UNK` declarations: unknown callee return types in the individual wrappers and unknown global objects in the consumers and grouped sources. No unknown type was silently filled in. Consequently the secondary compile counts do not isolate consumer code generation, and the unmodified sources cannot support a byte-exact or runtime correctness claim. Both raw and lowered compiler sources, complete m2c output with emitted declarations, body diffs, preprocessing receipts, all failures and the secondary private database are retained under `portable/secondary/`. `verify.py` additionally audits all eight secondary records, receipt digests and unchanged bodies/definition signatures.

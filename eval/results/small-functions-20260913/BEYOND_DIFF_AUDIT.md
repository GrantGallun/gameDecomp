# Beyond-diff audit of small unresolved functions

Snapshot: checkpoint **13212**, using `inventory.json` and its pinned inputs. This is an offline audit; no campaign edits, compiler/model calls, or live database copy. Candidate source hashes were checked before reading. Fifteen selected candidate/target pairs were archived with matching source and target pin hashes; `__ll_rshift` has no selected source/pinned workspace and was not reconstructed here. Exact inputs and compact metadata are in `beyond-diff-inputs/` and `beyond-diff-evidence.json`.

## Findings

The <=256-byte, <=3-cluster-member unresolved cohort contains **78 functions**, including 40 <=128 bytes. Only **7/78 have neither known callers nor callees**. Small layout clusters are not evidence of isolation. For example, `sprintf` has 40 recorded callers and `__osSiRawStartDma` has eight. The separate <=128-byte/no-known-edges cohort has **31 functions**, many of them callbacks.

Current source-bound semantic records in the weak-cluster cohort are: 10 observed_pass, 23 observed_pass_with_execution_debt, 9 observed_failure, 15 unavailable, and 21 absent. All 57 present semantic records name the selected source SHA. These statuses are not interchangeable: a finite synthetic pass is not whole-game equivalence, and unavailable tests do not imply incorrect C.

### 1. Linking and target-range evidence can explain a small residual before another source rewrite

Three weak-cluster functions have 100 similarity while remaining pending: `osCreateMesgQueue`, `drawRaceMotionAnimationDebugViewerMotionNumber`, and `fadeOutMultiplayerCourseSelectMenu`. `osCreateMesgQueue` has zero recorded residual fault categories, an empty first diff, and 64 passing semantic cases. Its object/ROM certificate is the next place to inspect, not a new logic guess. Root is independently auditing that certificate path.

The pinned `initMainMenuSceneModelRenderer` workspace target contains two `jr ra; nop` pairs even though the inventory range is eight bytes; selected C is an empty function, and the residual lists extra target instructions. This is an explicit function-range/padding/neighbor-boundary question. Do not enlarge the C body just to absorb adjacent code without an address-range/whole-object check.

### 2. MMIO is two separate problems: source/object representation and executable device state

**14/15 unavailable** weak-cluster cases explicitly require hardware-register environments (SI, SP, AI or PI). The remaining unavailable function is `alSavePull`.

The selected `osAiGetLength` source declares `extern u32 AI_LEN_REG` and returns it. Target instructions access the encoded address 0xA4500004 through t6; the current residual uses v0 as the address register, giving 96.667 similarity. `__osSpSetStatus` also scores 96.667, but its residual is symbolic `%hi/%lo(SP_STATUS_REG)` versus absolute 0xA4040010 addressing. These need different remedies. A compiler representation probe can solve register allocation; a ROM/link-address certificate can establish an absolute-symbol equivalence. Neither requires pretending a peripheral register is ordinary synthetic RAM.

Existing `hardware_environment.obligations` deliberately blocks runtime execution and records exact header/assembly witnesses. Main-tree `register_views` can propose encoded-address volatile scalar accesses, but still does not supply a device model. Keep exact object/whole-ROM verification independent of semantic availability. If runtime support is added, begin with an explicit read/write event environment for a closed register wrapper, validated against a device/emulator trace, and retain timing/DMA effects as debt.

### 3. Indirect dispatch supplies real evidence missing from direct-call topology

A hash-checked scan of pinned binary data assembly found actual address entries:

- `asm/data/libmus/player.data.s`: `.word Fcutoff`, `.word Fendit`, `.word Fdrums`, with encoded addresses 0x8009CF30, 0x8009CF1C, 0x8009D308.
- `asm/data/race/camera/race_camera.data.s`: entries for `initRaceCameraCourseStart`, `initRaceCameraFixedPositionFollow`, `initRaceCameraChase`, `initRaceCameraRotationTransition`, and many `initRaceCameraPositionTransition` slots.

Full locations, line contexts and hashes are in `binary-data-callback-refs.json`. This establishes **address-taken table membership**, not a caller, signature or effects certificate by itself. The next evidence step is to bind each table load and `jalr` site to the table range, selector calculation and register/stack arguments, then combine that with the leaf's loads/stores and return uses. Keep candidate indirect edges distinct from proven direct calls.

`Fcutoff` is only 32 bytes, but its assembly already supplies a useful contract: two unsigned byte reads from a1 form a big-endian halfword; it writes offsets 0xC2 and 0xC4 of a0; it returns a1+2. Current C uses `PlayerCommandState` and returns the pointer as s32. Dispatch return-use evidence could constrain pointer versus integer ABI and the command cursor, without naming unknown fields. A bigger prompt containing unrelated callees would add little.

Existing `callback_abi` binds header-measured callback slots using binary pointer provenance and admits argument-word comparisons only. It explicitly does not establish effects or concrete execution; `callsite_contracts` focuses on completed callee contracts flowing into unresolved callers. The demonstrated missing topology is the reverse path from binary data-table entries through dispatch sites to these apparently isolated leaf contracts.

### 4. Valid object graphs matter more than more random mutations for some tiny functions

`alSavePull` is 140 bytes and 98.235 similarity, yet its saved exploration tried **1005 target inputs and completed none**. The first failure is a pointer chase: target loads a0 from `[a0]`, then the callback from `[a0+4]`; the synthetic first load produces an unmapped pointer. Its fifth argument is an outgoing command-buffer pointer. Current C explicitly contains that same callback chain and the post-call 32-byte command writes.

An appropriate next test needs a source-independent bound object graph for the first object, callback target/ABI, command buffer extent and callback return pointer, preferably from a captured caller entry or ROM-bound constructor/dispatch evidence. Seeding random integers or a guessed callback output can make the test run while hiding the missing effects contract. Existing runtime capture only supports a narrow integer-leaf entry pilot, so this callback function needs a deliberate extension, not a claim that general game-state capture is already available.

### 5. Small ABI reconstruction failures are not byte-polish problems

`sprintf` remains compile-blocked with `s8 arg2, ? arg3, ...`. Its target spills a0..a3 and passes `sp+0x28` (the saved a2 slot) to `_Printf`; these are strong varargs-area witnesses. Caller argument uses plus the target compiler's supported varargs mechanism can constrain a bounded ABI repair. The forty direct callers make this valuable beyond this one function. Headers can assist, but should remain labeled assistance.

`ldiv` has a decompiler pointer draft with undefined `sp` and `arg0->unk*`, while assembly calculates quotient/remainder into adjacent stack words, copies both to a0, and returns a0. This strongly motivates a two-word aggregate/hidden-result ABI hypothesis and a target-compiler probe. It does not prove the precise original public C type. The explicit `break 6/7` arithmetic guards should not be mistaken for a hardware-only function classification.

Compiler recipe reconstruction already reads assignment-only Makefile metadata for compiler/ISA/optimization/postprocessing settings, without original C. A bounded microprogram fingerprint or compile probe can test ABI lowering and scheduling under that recipe. Broad SDK source-template import would mix library recognition with held-out answers and is unnecessary for these evidence-backed probes.

### 6. Some tiny ranges are backend work

The no-edge group includes `entrypoint`, exception handlers and `__ll_rshift`; the weak group includes COP0/cache/TLB primitives and RSP boot code. Ordinary C source repair is not the right universal lane. Existing `sdk_intake` distinguishes explicit machine-operation blockers from its narrower straight-line/control-flow admission limits and insists on ROM-slice reassembly. Missing intake for a branch or arithmetic guard is a backend dialect limitation, not proof that matching C is impossible. Do not claim independent reproduction of `__ll_rshift` here: no pinned workspace was available in this snapshot.

## Suggested order

1. Inspect certificates/ranges for the three 100-score cases and the eight-byte empty-function boundary before spending more model calls.
2. Run bounded compiler/link-address probes for simple MMIO wrappers; keep the hardware runtime gate honest.
3. Add a binary table-membership/dispatch evidence packet, then one caller-to-leaf ABI pilot on an audio command handler. This is the strongest demonstrated evidence missing from the direct-call graph.
4. Pilot varargs/aggregate-return ABI reconstruction for `sprintf`/`ldiv` with the real target compiler.
5. Extend captured object graphs/callback effects for `alSavePull` only after provenance and ownership of those inputs are explicit.

These are scoped proposals, not changes deployed by this audit. No universal semantic or SDK-identity claims are made.

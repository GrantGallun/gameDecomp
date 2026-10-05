"""Catalog of IDO codegen patterns.

Where there is a pattern there is a function, and where there is a function
there is use. Every recurring shape we identify in compiled output gets an
entry here rather than an ad-hoc fix buried in whatever module first tripped
over it.

An entry earns its place by being *actionable* in one of three ways:

    evidence  - it changes how raw observations should be interpreted
                (a bulk-copy word says nothing about field width)
    solver    - it tells the refine loop what C shape produces this asm
                (a magic multiply means `/ 10` in the source)
    review    - it is a signal a human or model should weigh, not a rule

Each entry records where it was CONFIRMED. A pattern asserted from intuition
and never checked against the finished decomp is exactly the class of bug this
project keeps catching in itself -- see "How to not ship analysis bugs" in
CLAUDE.md. `confirmed_on` empty means "hypothesis", and hypotheses do not get
to change behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Pattern:
    id: str
    name: str
    kind: str                    # evidence | solver | review
    looks_like: str              # the shape in compiled output
    means: str                   # what it actually is in the source
    prescription: str            # what code should do about it
    confirmed_on: list[str] = field(default_factory=list)
    detector: Callable | None = None

    @property
    def is_hypothesis(self) -> bool:
        return not self.confirmed_on


CATALOG: dict[str, Pattern] = {}


def register(p: Pattern) -> Pattern:
    if p.id in CATALOG:
        raise ValueError(f"duplicate pattern id: {p.id}")
    CATALOG[p.id] = p
    return p


# ------------------------------------------------------------------ confirmed

register(Pattern(
    id="text-alignment-padding-is-not-function-body",
    name="Zero words after a function's last control transfer are .text alignment, and mark a boundary",
    kind="evidence",
    looks_like="A function's last control transfer (`jr`, `j`, `b`, `eret`, or a no-return `jal`) and its delay slot, then one to three `nop` words reaching a 16-byte boundary, then the next object's code.",
    means="Each object's .text is 16-byte aligned; the linker fills the gap with zeros. The compiler's .size excludes them, so a function reassembled with them is not byte-exact (bootThreadMain sat one padding word from exact). spimdisasm folds them into the preceding function. When the following code opens a stack frame (`addiu sp, sp, -N`), the padding is also the only evidence of where that function starts if nothing calls it.",
    prescription="disasm.functions.split_padding cuts trailing zeros that follow a control transfer's delay slot (the delay-slot nop itself is kept); zeros after ordinary code are NOT padding (an IDO -mips1 load-delay nop). disasm.functions.padding_boundaries seeds a function start at a 16-aligned prologue after such padding; a leaf after padding is left unknown rather than guessed.",
    confirmed_on=[
        "2026-10-03 SBK1 boot segment, graded against the reference ELF (disasm.grade): 168 functions carry split padding and all 162 size disagreements of the unsplit spimdisasm baseline became exact; osPfsIsPlug (never called) is found only through the padding after __osCleanupThread's `jal osDestroyThread`. tests/test_disasm_frontend.py.",
    ],
))

register(Pattern(
    id="object-functions-in-reverse-source-order",
    name="Some objects lay out their functions in exactly reverse source order",
    kind="review",
    looks_like="Within one object's .text, the global functions' addresses descend in the order the C file defines them.",
    means="Measured, cause not established: on SBK1, 98 objects are in forward source order and 9 in exactly reverse order (libultra's env.c among them: _ldexpf < _frexpf < alEnvmixerParam < alEnvmixerPull). Which compiler flag or build step produces this is a hypothesis to test with rule probes, not a fact.",
    prescription="Never place functions (or recover static functions) by source order. disasm.grade places statics from address-side intervals instead and counts orders in Reference.object_order. A splitter that assigns names or files by order must allow both directions.",
    confirmed_on=[
        "2026-10-03 SBK1: disasm.grade.reference object_order {'forward': 98, 'reverse': 9}; env.c checked by hand against ELF addresses.",
    ],
))

register(Pattern(
    id="assignment-scoped-field-cache-can-couple-unrelated-register-webs",
    name="A later cached field assignment can keep an earlier temporary's allocation constraints",
    kind="solver",
    looks_like="A scalar temporary serves unrelated values in different regions; a later field cache contributes an allocation residual.",
    means="On the exposed searched AerialTrick parent, replacing only the timer assignment's reads with explicitly converted direct field accesses produced a certified match. Earlier velocity uses and the declaration remain. This confirms one intervention, not a general liveness theorem or independent discovery.",
    prescription="Offer solver.scoped_field.variants through the shared regalloc_mutations stream. Preserve assignment conversions; decline visible calls, escapes, intervening possibly aliasing stores, volatile declarations and unsupported control flow. Headers are not expanded, so these source guards do not prove equivalence. Require the existing source-bound object certificate and frontend pass. scoped_fields=False provides a controlled ablation.",
    confirmed_on=[
        "2026-09-28 updateRacePlayerMode48AerialTrick: source d3b676805330b2b207fb4ba26d757518b318d6b937f4cdd8be271c877266e1cd from the previous continuation, no reference C. Baseline plus two source-only generator proposals: temp_v0_2@3110:s32:preserve-conversion yielded [0,0,0] and exact certificate plus frontend pass. Three native calls. Previously exposed, project headers, earlier lineage unknown; not another newly discovered function. Receipts: eval/results/scoped-field-20260928/control-receipts.json.",
    ],
))

register(Pattern(
    id="ordered-scalar-temporaries-can-split-an-ido-register-web",
    name="Reusing a scalar local can join allocation webs left separate by m2c temporaries",
    kind="solver",
    looks_like="Near-exact code loads successive values into distinct scalar temporaries while the object residual is register allocation.",
    means="On two exposed development cases, merging a later s32 temporary into an earlier s16 local produced the target object. Textual use order is only a proposal heuristic, not a liveness or semantic-equivalence proof; a type-changing merge may alter conversions. This does not establish that selection policy is irrelevant or that the old mutation graph cannot reach a match.",
    prescription="Offer bounded scalar_coalesce variants through regalloc_mutations.variants(coalesce=True). Reject unsupported visible bindings, address escapes and backedges; retain source/type labels. Require the ordinary frontend pass plus source-bound byte certificate. Compare production and evolvability with and without this family before campaign promotion; residual routing stays fixed.",
    confirmed_on=[
        "2026-09-28 stepRaceMotionLoopingAnimation and stepRaceMotionLoopingJointAnimation: frozen m2c roots from evolvability-trial-20260928/bundle-1 (attempts 255793 and 260195); final native controls each offered temp_h0->temp_v0:s32->s16 and certified exact with passing frontend. Two baseline plus two candidate compiles; both header-assisted, previously examined development cases. /home/grant/decomp/experiments/coalescing-factorial-20260928-final/controls.json; replay script and receipts in eval/results/coalescing-factorial-20260928/. No reference C or prior winner was supplied.",
    ],
))

register(Pattern(
    id="exclusive-pointer-locals-can-split-an-ido-register-web",
    name="Merging pointer locals across exclusive arms can propagate a register constraint",
    kind="solver",
    looks_like="The same pointer role uses v0 in one candidate branch and v1 in another, while the target uses v1 in both; instruction shapes and other operands already agree.",
    means="Separate C locals can create separate allocation webs. In the measured candidate, the first arm's pointer had no conflict with v0, while the other arm's pointer did. Reusing one compatible pointer local across those exclusive arms produced the target allocation without changing the original literal-zero store. This is a confirmed intervention on one development function, not a reconstruction of its original C or a general transfer result.",
    prescription="Offer a bounded local_web_merge candidate for compatible plain pointer locals confined to opposite if/else arms. Reject ambiguous scope, shadows and address escapes. Compile the generated source and inspect all collateral changes; ordinary frontend and object-section certificate acceptance remain required.",
    confirmed_on=[
        "2026-09-26 releaseSoundEffectHandleNode: retained attempt 108368 reproduced 99.615; private followup.sqlite receipt 6 merged temp_v1_2 into temp_v1, scored 100 with empty diff, passing frontend and exact byte certificate. eval/results/frontier-run-20260926/register/RESULT.md and WEB_MERGE_PROTOCOL.md. Project-header-assisted development case; no reference or previous winning C used.",
    ],
))

register(Pattern(
    id="do-token-refusal-forces-a-non-equivalent-lowering",
    name="Refusing the `do` token before IDO runs turned matching sources into near misses and hid every refused candidate's real error",
    kind="evidence",
    looks_like="A candidate is rejected with `ERROR: The C file contains a do-while loop.` -- a message from the per-function build helper, before the compiler is invoked. The ROM-verified source for the same function often uses `do { ... } while (...)`; the helper demanded it be rewritten as `for (;;) { ...; if (!(...)) break; }`.",
    means="The refusal was a POLICY, not a compiler limit, and it was wrong in three separate ways at once. Measured 2026-09-17. ONE, it changed codegen: on drawRaceSplitscreenSelectOption2Frame the reference body with `do` compiles BYTE-EXACT (100.000) while the mandated lowering of that same body scores 99.395 (`regalloc=8 ordering=5 structural=1`), and a further pipeline edit reaches only 99.936 -- so the ban was the entire residual of the five 99.936 `drawRaceSplitscreenSelectOption*` / `drawCharacterSelectCoursePreviewPanel*` siblings that a session of work had been treating as an unexplained register/ordering wall. TWO, it hid the truth about everything it refused: the check runs before IDO, so 845 attempts across 97 functions recorded a policy error where a compiler error belonged, and re-scoring them with the ban gone gives 7 compiling and 0 exact -- their real failures were ordinary C89 syntax errors the taxonomy never saw. THREE, the exposure is not small: the reference source contains 390 `do {` occurrences across 236 functions, of which 54 were live residue and 168 had never been attempted.",
    prescription="The refusal is REMOVED (`eval/remove_do_ban.py`, applied to the checked-in helper, the live helper and the 2,015 per-workspace adapted `.compiler-*.sh` that are actually invoked at score time; per-workspace `build.sh` is a symlink to the live helper). `solver/rewrites.restore_do_while` is the exact inverse of the lowering and `do_while_restore_rewrites` offers it to `regalloc_search` as the `do_restore` family, so BOTH spellings are proposed and the oracle decides -- the lowering is a fallback, never a mandate. Do not reinstate a token refusal without evidence that the lowering is codegen-neutral: the round-trip is pinned in `tests/test_do_while_restore.py`, and the counterexample is one compile away.",
    confirmed_on=[
        "bisect from the key 2026-09-17, drawRaceSplitscreenSelectOption2Frame: K0 = reference body with `do` -> exact 100.000; K1 = same body lowered -> 99.395; K2 = K1 + the pipeline's guard inline -> 99.936; C = stored candidate -> 99.936. K1 and K2 reproduce the recorded attempts exactly. eval/results/rename-wall-20260917/bisect_from_key.py",
        "population 2026-09-17, eval/do_while_population.py: 390 `do {` occurrences, 236 functions, of which 54 live residue and 168 never attempted against kb-sbk1.sqlite.",
        "cost of the refusal 2026-09-17, eval/do_ban_rerun.py: 845 attempts across 97 functions carried the policy error; re-scored as written, 7 compile and 0 are exact. eval/results/do-ban-rerun-20260917/",
        "yield of the inverse 2026-09-17, eval/do_restore_search.py over the 54 live do-bearing functions: 20 restorable sites, 13 compiled, 2 EXACT -- func_80063A9C (88.261 -> 100.0, candidate origin `dag-pipeline-census-root`, so CAPABILITY) and updateRaceGameplayFlow (99.346 -> 100.0, candidate origin `authorized-target-history-recovery`, so recovered). func_80063A9C re-verified independently: 245/245 instructions, empty diff. eval/results/do-restore-20260917/",
    ],
))

register(Pattern(
    id="m2c-type-placeholder-blocks-the-whole-translation-unit",
    name="m2c's `?` type placeholder makes cfe stop at that line and judge nothing after it, so ONE unresolved type masks the entire draft",
    kind="evidence",
    looks_like="cfe reports `Syntax Error` + `Empty declaration specifiers` at the first declaration m2c could not type -- `? lldiv(s32 *, s32, s32);` at line 12 of `_Litob`, `? sp30;` inside a body, `? *var_s3;` for an unknown pointee -- and truncates the error list there.",
    means="The draft does not merely have a bad line: the parse stops, so every later defect in the file is invisible. This is why the admission taxonomy looked like a wall of syntax errors. The class is measured: 122 drafts in `~/decomp/sbk1/nonmatchings` carry a `?` declaration, and 1,962 logged attempts died with `Empty declaration specifiers`.",
    prescription="`solver/m2c_placeholders.rewrite` substitutes a concrete type, evidence first (a name with a KB width keeps it) and `s32` otherwise; `variants` bounds the enumeration for the cases where the object disagrees. `tests/test_m2c_placeholders.py` asserts it FIRES on all four emitted shapes and declines on clean source and on a `?` in prose. NOTE the first version of the pattern required the `?` to be followed directly by an identifier, silently skipped `? *var_s3;`, and the sweep then reported 'the error moved rather than cleared' on six drafts where the line was still sitting there -- the silent-decline failure mode again. Resolving the placeholder does NOT by itself admit the file: the errors advance to the next class (`bitwise` casts, undeclared parameter types, a `? sp30;` later used as `sp30.unk0`, so `s32` is the wrong answer for it).",
    confirmed_on=[
        "population 2026-09-17, eval/m2c_placeholder_census.py: 122 drafts carry a `?` declaration; 1,962 attempts have `Empty declaration specifiers` in their error text.",
        "residual 2026-09-17, eval/placeholder_resolution_sweep.py: rewriting the placeholder alone admits 0 of 8 sampled drafts; eval/placeholder_admission.py composes it with the header route and still admits 0 of 8, because each fix exposes the next defect class. eval/results/placeholder-admission-20260917/RESULT.md",
        "silent decline 2026-09-17: the first DECL_LINE pattern missed `? *var_s3;` and reported no change while leaving the blocker in place, caught by reading the compiler's caret output rather than the summary count.",
    ],
))

register(Pattern(
    id="a-blank-draft-is-a-stale-artifact-not-an-m2c-limit",
    name="`// file is blank because m2c failed to decompile function` records a failure from whenever the workspace was bootstrapped, not a current one",
    kind="evidence",
    looks_like="`base.c` is 79 bytes: an include plus that comment. The object compiles to no text symbols, so the scorer reports `Compiled object has no text symbols. Check for type conflicts or include issues.` -- which reads like a recipe bug.",
    means="`tools/claude` writes that comment when m2c exits non-zero and then DISCARDS the diagnostic (`echo` to scrollback), so the reason is recorded nowhere and the blank is indistinguishable from a capability gap. Re-running the identical invocation against each workspace's own `target.s` with the m2c installed now succeeds on 66 of 79: the drafts were stale, not impossible.",
    prescription="`eval/m2c_refusal_probe.py` re-runs the bootstrap invocation and keeps the stderr; `eval/m2c_redraft.py` regenerates the drafts atomically, preserving the original. Treat a blank draft as a routing signal (needs m2c re-run or a model call), never as four compiles of evidence that the function cannot be drafted.",
    confirmed_on=[
        "population 2026-09-17, eval/blank_draft_census.py: 79 of 2,066 workspaces have a draft with no statements, all 79 carrying the literal marker.",
        "re-run 2026-09-17, eval/m2c_refusal_probe.py: 66 of 79 now exit 0; the remaining 13 are `other` 11, `jump-table` 1, `directive` 1. eval/results/m2c-refusal-20260917.json",
    ],
))

register(Pattern(
    id="admission-buys-scoreability-not-matches",
    name="The header/repair admission route takes a never-compiling draft to 80-93% and never to exact",
    kind="review",
    looks_like="`eval/header_admission.py` admits ~22% of the never-compiling stratum with an include round plus `zero_token_harvest.repair_chain`, and the admitted candidates score 78-93.214 while none is exact.",
    means="Measured 2026-09-17 on the most favourable sample the run can produce -- its four HIGHEST scorers, since conversion can only get easier as the score rises. Through `solver/repair`'s deterministic rungs: `__osSiRawStartDma` 93.214 -> 93.214, `MusStop` 86.361 -> 87.889, `__osSetTimerIntr` 83.393 -> 83.393, `MusHandleAsk` 80.810 -> 80.810. Four of four stay non-exact, so the population does not convert either. Admission's product is a SCOREABLE draft: eligible for triage and for a model-refine loop, and no longer an invisible row.",
    prescription="Do not size an admission campaign by its admit rate. Size it by conversion, and measure conversion on the highest scorers first -- a run whose best four do not convert does not need 1,633 more rows to find out. Spend the model-call budget on admitted-but-not-exact drafts, which is the only thing admission actually unlocks.",
    confirmed_on=[
        "conversion 2026-09-17, eval/repaired_admission.py on the four highest scorers of eval/results/header-admission-full-20260917: 0 exact, best gain +1.5. eval/results/repaired-admission-20260917/state.json",
        "admission rate 2026-09-17, eval/header_admission.py stopped at 112/1633 with {not-compiling: 94, compiled: 17, raised: 1}.",
    ],
))

register(Pattern(
    id="bootstrap-context-carries-the-reference-source-declarations",
    name="`tools/claude` builds m2c's `ctx.c` from the project's own fully-matched `src/**.c`, so an m2c draft may already contain the reference struct layouts",
    kind="evidence",
    looks_like="An m2c draft uses a type name that no header declares and that assembly cannot supply -- `CourseGridEntry *var_v0;` in `clearRaceReplayCourseGrid`, `RaceCourseSurface *var;` in the five `*RaceCourseSurface*` drafts -- and `header_admission.declaring_header` returns None for it.",
    means="`tools/claude` sets `C_FILE=\"src/${RELATIVE_DIR}.c\"` and runs `python3 tools/m2ctx.py \"$C_FILE\"`, passing the result to m2c as `--context`. For a 100%-decompiled target that file IS the reference answer, so the declarations m2c was handed include the reference struct bodies. Traced mechanically 2026-09-17 over the never-attempted drafts: of 75 distinct type names, 53 are declared by a project HEADER and 21 ONLY by a project `.c` -- and for each of those 21 the declaring `.c` is the very source file of the function being drafted (`CourseGridEntry` -> `src/race/flow/race_flow.c:54`, which is where `clearRaceReplayCourseGrid` lives). One name (`Mtx_t`) is declared nowhere.",
    prescription="`eval/match_claim_audit.py` collects every non-primitive type name a source mentions, asks where each is declared, and reports a tier CEILING: a match resting on a header-declared type is at most `header-assisted`, and one resting on a `.c`-only type is not independent capability at all. Run it on any candidate whose draft predates 2026-09-17 before calling it SOLVED. Re-drafting with m2c invoked WITHOUT `--context` produces genuinely independent drafts and is what `eval/m2c_redraft.py` does; its three matches audit `unqualified`.",
    confirmed_on=[
        "provenance 2026-09-17, eval/type_name_provenance.py over 1,197 never-attempted drafts: 75 type names, 53 header-declared, 21 project-.c-only, 1 nowhere; 174 functions fully header-resolvable, 31 carrying a header-less name. eval/results/type-name-provenance-20260917-v2.json",
        "per-name trace 2026-09-17, eval/match_claim_audit.py --types: all 21 header-less names resolve to a project `.c`, each the drafted function's own file. eval/results/match-claim-audit-20260917.json",
        "audit of this session's four matches 2026-09-17, eval/match_claim_audit.py --functions: `loadMainMenuSceneModelAnimationBank`, `__osViGetCurrentContext`, `alFxParam`, `resetRenderScratchAllocator` all report ceiling `unqualified`, so the SOLVED claims stand.",
    ],
))

register(Pattern(
    id="declaring-header-must-know-every-typedef-spelling",
    name="A provenance detector that returns None is not neutral: it routes a resolvable name to the evidence-free path",
    kind="evidence",
    looks_like="`declaring_header(repo, 'Mat3x3')` returns None, so a function using it is counted as carrying a type no header declares -- while `include/game/math/geometry.h:23` says `typedef s16 Mat3x3[9];`.",
    means="Only `typedef struct Name {` and `} Name;` were recognised. The project also writes plain `typedef <base> Name[...];` with no braces. Measured 2026-09-17: this silently misclassified 3 of 75 names (`Mat3x3`, `Gsettilesize`, `Gloadtlut`) as evidence-free. The failure is worse than a missed fix because None is the SIGNAL used to route a name to the `unk`-only path -- the silent-decline failure mode applied to provenance.",
    prescription="Recognise four spellings: `typedef struct Name {`, `} Name;`, a no-brace `typedef ... Name ...;` where the name is the last identifier before the `;` (array suffix allowed), and a bare `struct|union|enum Name`. The no-brace form must not match `typedef s32 (*Fn)(SomeType *);`, where the name is only a parameter -- pinned in `tests/test_declaring_header.py` along with `initRaceCourseSurfaceData`, which is not a declaration of `RaceCourseSurface`.",
    confirmed_on=[
        "spelling 2026-09-17: `include/game/math/geometry.h:23` is `typedef s16 Mat3x3[9];`, and `drawRacePlayerModel-2`'s draft uses `Mat3x3 rotation;`.",
        "effect 2026-09-17, eval/type_name_provenance.py before/after: 50 -> 53 names resolved, 25 -> 22 header-less. tests/test_declaring_header.py, 7 tests.",
    ],
))

register(Pattern(
    id="a-contaminated-draft-is-fixed-by-re-drafting-not-by-rewriting",
    name="For an m2c draft that carries a project-`.c`-only type, stripping the type launders the knowledge; re-running m2c with no `--context` is the only instrument that removes it",
    kind="solver",
    looks_like="A draft uses a type no header declares (`CourseGridEntry *var_v0;`) because `m2ctx.py` fed m2c the function's own reference source. The obvious repair is to declare the type `unk`-only and rewrite the member accesses as byte arithmetic.",
    means="That repair is the same knowledge laundered: the offsets and member names in the draft came from the reference layout, so byte arithmetic over those offsets proves nothing about what the binary could have told us. Measured 2026-09-17 over all 31 affected drafts, re-run with m2c invoked WITHOUT `--context`: 29 drafts produced (2 refused), **0 compile**, 28 not-compiling, 1 skipped for a suffixed workspace. The mechanism does fire -- no re-drafted source uses any project type as a type; `clearRaceReplayCourseGrid` goes from `CourseGridEntry *var_v0;` / `var_v0->status` to `s16 *var_v0;` / `*var_v0` with `var_v0 += 0x10`. The context was doing the work: without it the drafts do not compile at all.",
    prescription="Use `eval/m2c_redraft.py --provenance <type_name_provenance>.json --sidecar --admit`, which writes `base.contextfree.c` BESIDE `base.c` -- the contaminated draft is the evidence and is not overwritten. Do not spend effort on an `unk`-only rewrite of a contaminated draft; it cannot yield a SOLVED claim either way. For those functions the honest options are a model draft or the reference layout, and the latter is `header-assisted` at most.",
    confirmed_on=[
        "outcome 2026-09-17, eval/m2c_redraft.py --provenance over 31 drafts: 29 re-drafted, 0 compile, 28 not-compiling, 2 m2c refusals, 1 suffixed workspace. Residuals: Syntax Error 26, Selector-requires-struct 2. eval/results/contextfree-redraft-20260917/state.json",
        "firing 2026-09-17: no sidecar draft uses a project type as a type; `clearRaceReplayCourseGrid/base.contextfree.c` uses `s16 *var_v0;` and `var_v0 += 0x10`.",
        "false alarm worth recording: a substring grep said 5 sidecar drafts still contained the project type, and all five were the function's own name (`findRaceCourseSurfaceFromHint`). Check the type used AS A TYPE, not the string.",
    ],
))

register(Pattern(
    id="header-assisted-tier-is-keyed-on-a-strategy-string-not-on-the-source",
    name="The SOLVED/header-assisted split reads the winning attempt's strategy label, so a route that is a header route by construction lands in SOLVED",
    kind="review",
    looks_like="`eval/status.py` computes `header_assisted` as `a.strategy like '%project-header%'`. `eval/header_admission.py` logs `strategy=\"header-admission:*\"`, which does not contain that substring, so its matches are counted as SOLVED.",
    means="Measured 2026-09-17 on the two matches the header route produced: `loopMainMenuSceneModelAnimation` (uses `MainMenuSceneModel`) and `notifySchedulerClients` (`SchedulerClient`, `SchedulerState`), both header-backed per `eval/match_claim_audit.py`, both therefore ceiling `header-assisted`, both counted as SOLVED. The route's own definition is 'a reconstructed `include/game/**` header supplies the prototype and the layout', so the label and the route disagree by construction. **THE WHOLE-SET CHECK IS NOW DONE.** `eval/match_claim_audit.py --from-db` audits the winning exact attempt's own `source_code` for all 319 exact functions, and 139 of the 261 labelled SOLVED dereference a member through a header-declared type. Corrected split approximately 114 SOLVED / 150 header-assisted / 55 recovered, byte-exact unchanged at 319. 45 further `header-assisted` rows sit inside `recovered`, which is already excluded from SOLVED and must not be double-booked into the correction. 119 of the labelled-SOLVED take no struct layout at all (72 wholly unqualified plus 47 that name a header type and contain no `->`), and that part survives the strictest reading.",
    prescription="The classification is deliberately NOT changed. `eval/status.py` states the precondition for changing it -- the check must run across the whole matched set rather than be applied to one match -- and that precondition is now met, so applying it is a bounded change rather than a guess. It stays the operator's call because it moves the headline by 139 and because the label rule is the project's published definition, not a defect. Until then: quote 319 byte-exact, and never quote 253 SOLVED without saying it is label-based. The ratchet is byte-exact and byte-exact is 319 under every reading.",
    confirmed_on=[
        "mechanism 2026-09-17, eval/status.py:130-135 and the comment at 118-129.",
        "whole set 2026-09-17, eval/match_claim_audit.py --from-db over 319 exact functions, 0 missing sources: ceiling 186 header-assisted / 52 mentions-only / 81 unqualified; cross-tab SOLVED-by-label -> 139 header-assisted, 50 mentions, 72 unqualified; recovered -> 45 / 2 / 8. eval/results/claim-audit-whole-set-20260917.json",
        "instances 2026-09-17, eval/match_claim_audit.py --from-ledger over all 91 cohort nodes: 27 header-assisted, 41 mentions-only, 23 unqualified; 38 of the 41 contain no `->` at all. eval/results/claim-audit-all-cohorts-20260917.json",
        "known under-count 2026-09-17: the member-access test looks for a LOCAL declaration, so it misses `gCurrentGameTask->fade` where the global's type comes from a header -- confirmed on `returnToRaceTypeSelectMenu`. 139 is a floor.",
    ],
))

register(Pattern(
    id="saved-order-has-a-gradient-and-no-generator",
    name="A `saved_order` residual is a reordering the positional pairing cannot see, and no source permutation moves it",
    kind="evidence",
    looks_like="A residual whose `regalloc_signature.compare` reports the `saved_order` signature -- substitutions `{s2->s3: 1, s3->s2: 1}` on two same-shape instructions, e.g. `move s2,zero` / `move s3,zero` -- while `signals.analyse` reports `regalloc=0` and books it as `ordering=2`. The whole compiled residual is those two lines exchanging places.",
    means="THE RESIDUAL IS AN ORDER DIFFERENCE, NOT A REGISTER RENAME, and the earlier reading of this entry ('a pure register rename that neither instrument owns') was wrong. `compare` aligns on register-free `shape`, so `move s2,zero` and `move s3,zero` are interchangeable to it and a source that emits them in the other order is scored as a two-register swap. The two dumps are consistent with either story positionally -- but not globally: every OTHER use of s2 and s3 in these dumps is byte-identical between target and candidate (`addu t2,t0,s2` / `lhu a3,0x20(t2)` for the tile index, `addu a1,t5,s3` for the offset), so s2 holds the tile index and s3 holds the offset in BOTH. No value moved between registers. The only difference is which of the two zero-initialisations IDO emitted first. `solver.regalloc_signature` now reports this split directly: `reordered = common - same_position`, `renames = length - common`, and on all five cases `reordered=2, renames=0` with `order_only=True`. Measured 2026-09-17 on the five 99.936 siblings; census over the 133 compiling non-exact functions: 24 `reordered-only`, 95 `renamed-only`, 13 both, 1 no-register-difference.",
    prescription="Do not route these to `regalloc_search`, and do not build a rename counter that expects it to help. MEASURED: the five cases through `close_nearmiss --force-regalloc` at budget 300 gave `[0,2,2] -> [0,2,2]` on every one, `best_label = baseline`, 0 of 5 exact -- 1,500 compiles and the gradient never moved, because there is no register difference to reduce. AND DO NOT ROUTE THEM TO THE ORDERING PASS EITHER. `rewrites.statement_order_rewrites` (the pass that owns the `ordering` class) DOES fire -- it yields three statement permutations naming `i`, `offset` and `tileIndex` -- and all three compile to a byte-identical object still carrying the swapped pair; seven hand-written orderings of the same statements (`offset` first, `offset` hoisted above the guard, `offset = tileIndex = 0`, `offset = 1 - 1`, `i = 0x80` moved, the `if ((1))` wrapper removed, declarations swapped) do the same. Source statement order does not control this emission order, so 'the target's order is a statement order' is FALSE for this class. What is measured instead is a coupling: in every variant tried, the value emitted FIRST holds the HIGHER saved register (baseline: tileIndex in s3 emitted first; best candidate: offset in s3 emitted first), and the target needs the first-emitted value in the LOWER one -- a state none of the families produce. The lever is unmeasured; the class is now measurable and the reachability of that state is bounded by `eval/results/rename-wall-20260917/`.",
    confirmed_on=[
        "rename-gradient probe 2026-09-17: drawCharacterSelectCoursePreviewPanel8 and drawRaceSplitscreenSelectOption2Frame both score 99.936, signals regalloc=0 ordering=2, regalloc_signature gradient [0,2,2], signatures {saved_order: 2}.",
        "re-pairing 2026-09-17: on all five siblings `reordered=2, renames=0, order_only=True`, and `reordered + renames == register_instructions` holds on every shape in tests/test_regalloc_signature.py. The surrounding uses of s2 and s3 are byte-identical in target and candidate, so no value changed register.",
        "route test 2026-09-17: the five sibling cases through close_nearmiss --force-regalloc at budget 300 -> 0 of 5 exact, gradient [0,2,2] unchanged, best_label 'baseline' on all five. eval/results/colouring-route-20260917/.",
        "ordering-generator FIRES, 2026-09-17: `statement_order_rewrites` yields 3 variants (move statement i / offset / tileIndex), all 3 compile, all 3 leave pair=[move s3,zero, move s2,zero] with gradient [0,2,2]. eval/results/rename-wall-20260917/ordering_probe.py.",
        "source-order invariance 2026-09-17: 7 hand-written orderings of the same two statements, including `offset = tileIndex = 0;` and `offset = 1 - 1;`, all compile to the same pair. eval/results/rename-wall-20260917/experiments.py.",
        "derivation refutation 2026-09-17: rule `same-shape-difference-is-a-permutation` (patterns/rules.py) claimed 5 cases, predicted 15, compiled 15, closed 0 -> NOT CONFIRMED by patterns/derive.py. eval/results/rename-wall-20260917/derive-refutation.json.",
        "census 2026-09-17, 133 compiling non-exact functions: reordered-only 24, renamed-only 95, both 13, no-register-difference 1. eval/results/rename-census-20260917/.",
        "reachability bound 2026-09-17: breadth-first enumeration of the whole generator set, no beam, from the baseline (depth 2, exhausted, 846 compiled) and from the best candidate (depth 3, capped, 1502 compiled) -> 2,348 compiled variants, 438 with the instruction structure intact, and 0 with the target's order and register binding together. eval/results/rename-wall-20260917/sweep-*.log.",
        "ordering pass over the census's 24 reordered-only functions, 2026-09-17: eval/order_search.py at depth 2 and cap 90 -> 481 compiles, 0 exact, best_label 'baseline' for 23 of 24. eval/results/order-search-20260917/.",
    ],
))

register(Pattern(
    id="target-linkage-static-inline",
    name="IDO rejects C99 `inline` and discards an unreferenced `static`: one bug, two symptoms",
    kind="solver",
    looks_like="A model-written target function declared `static inline` produces either `cfe: Syntax Error` at the opening brace -- pointing three errors deep and at the wrong line -- or, once `inline` alone is deleted, `ERROR: Compiled object has no text symbols. Check for type conflicts or include issues.`",
    means="`inline` is C99 and IDO 5.3 is a 1994 compiler, so the declaration specifiers parse wrongly and the brace is unexpected. Deleting `inline` is not enough: IDO does not emit a `static` function that nothing in the translation unit calls, so the object comes back with no `.text` and the build helper blames type conflicts, which is where the admission triage went looking. A game function has external linkage. The two symptoms were counted as separate failure classes -- 'syntax' (63 functions) and 'no text symbols' (33) -- and are one defect.",
    prescription="Apply `c89.to_c89` (which deletes `inline` and the other C99 spellings) AND `c89.public_definition`, which drops `static` from the TARGET function's own definition line and leaves helpers, tables and file-scope data alone. Deterministic, no model. `patterns/rules.py` registers it as `target-linkage-static-inline` with `criterion = compiled` -- it makes functions BUILD, it does not make them match, and the harness reports that distinction rather than burying it. NOT prefixed `ido-`: that prefix is reserved in this catalog for entries imported from the reference project's IDO knowledge, which tests/test_imported_ido.py requires to stay unconfirmed, and this rule is derived and confirmed here.",
    confirmed_on=[
        "kb-sbk1.sqlite attempts 31124 acquireRelocatableHeapBlockMetadata and 31125 addRacePlayerScore: the motivating pair. Raw = Syntax Error; to_c89 alone = no text symbols; to_c89 + public_definition = 75.833 / 85.455.",
        "patterns.derive confirmation run 2026-09-17, criterion=compiled, derivation_case=addRacePlayerScore: 7 claimed, 7 predicted, 7 compiled, and SIX of them are held-out cases the rule was not derived from -- acquireRelocatableHeapBlockMetadata, approachRaceIntroFlyoverOrbitRadius, dispatchRacePlayerMode30Attack, insertHuffmanQueueNode, removeHuffmanQueueNode, resolveAssetTableRelativePointer. Harness verdict: CONFIRMED. The other five declined because their non-compiling attempt fails for a different reason (undefined identifiers), which is the rule declining correctly rather than silently.",
    ],
))

register(Pattern(
    id="ordering-residual-states-the-statement-order",
    name="An ordering-only residual over independent STORES states the answer in the diff",
    kind="solver",
    looks_like="The instruction multiset is identical on both sides and a group of independent stores -- e.g. `sw zero,0x60(a0)` / `sw zero,0x68(a0)` / `sw zero,0x54(a0)` -- appears on both sides in a different ORDER, with `signals.analyse` reporting faults on `ordering` alone.",
    means="IDO emits independent STORES in C statement order, so the target's emission order IS the source statement order. The diff does not merely describe the problem, it states the answer: read the permutation off it and apply it to the statements. DERIVED, not searched -- which is why a permutation search fails here. `rewrites.statement_order_rewrites` yields SINGLE ADJACENT SWAPS from the baseline; the permutation that closes these functions is a COMPOSITION of several swaps, so no single variant from the baseline can be the answer, and 14 of them firing is consistent with none of them being right. SCOPE, measured: this holds for stores, NOT for loads. IDO schedules a load by where its value is USED, and the analogous source change on a load-reordering residual made it far worse -- see the refutation in confirmed_on.",
    prescription="For an ordering-only residual over independent STORES with DISTINCT operands, do not search permutations: read the target's emission order off the diff and rewrite the statements in that order. Do NOT extend this to a residual whose moved instruction is a LOAD, and do not extend it to a register exchange -- check ordering-diff-conflates-causes first, and check that the moved instructions are distinct rather than the same instruction with a different register.",
    confirmed_on=[
        "kb-sbk1.sqlite attempt 46494 Fstop: score 99.999 ordering=4 -> exact=True score=100.0 by reordering five independent NULL stores into the target's emission order (0x60, 0x68, 0x54, sh 0xbe, 0x14); no other change. Verified through the oracle.",
        "REFUTATION ON A HELD-OUT CASE (recorded, not hidden): updateRacePlayerMode16AerialTrick, attempt 22623, 99.712 ordering=3, whose moved instruction is a LOAD (`lw v0,0x44(s0)`). Applying the analogous change -- forcing the 0x44 read ahead of the 0x264 read -- gave 99.712 -> 97.115 with regalloc faults 0 -> 36, and the diff shows it altered the register of a DIFFERENT 0x44 load rather than moving the intended one. So the rule does not transfer from stores to loads; the cause discriminator correctly called both `order`, which is why the discriminator alone is not sufficient to route.",
    ],
))

register(Pattern(
    id="ordering-diff-conflates-causes",
    name="The same ordering diff has two causes, and one of them is register colouring",
    kind="review",
    looks_like="Two `move rX,zero` lines exchanged between target and candidate, with the two registers simply swapped -- `-move s2,zero` / ` move s3,zero` / `+move s2,zero`.",
    means="IDENTICAL surface diffs, DIFFERENT causes. On Fstop the cause was statement order and a statement reorder closed it exactly. On the five 99.936 siblings the same shape is an EMISSION-ORDER difference: `regalloc_signature` re-pairs the two same-shape instructions and finds the SAME two instructions on both sides (`reordered=2, renames=0`), and the surrounding uses of s2 and s3 are byte-identical in both dumps, so s3 holds the offset and s2 holds the tile index in BOTH and no register was reassigned. What IDO did differently is emit the two zero-initialisations in the other order. `signals.analyse` names the SYMPTOM (instruction order differs) and does not distinguish these; `patterns/ordering.classify` is POSITIONAL, so it names the sibling `colouring`, which is an inference from an ambiguous observation rather than a reading of it -- see `saved-order-has-a-gradient-and-no-generator` for the measurement and the refutation. This entry's earlier claim that the sibling is 'register colouring' is retained only as the name of the positional verdict.",
    prescription="Never route by dominant fault class alone, and do not treat `classify`'s `colouring` verdict as evidence that a value changed register -- check whether the two dumps contain the SAME instructions in the other order (`regalloc_signature.Report.renames == 0`), because if they do, the exchange reading is not the only one. Do not spend statement permutations on it: measured 2026-09-17, the ordering pass fires three variants on these and all three compile to a byte-identical object, and neither does `regalloc_search` own it -- all five went through `close_nearmiss --force-regalloc` at budget 300 and closed 0 of 5 with the gradient unchanged at [0,2,2]. So the cause is identified and NEITHER existing instrument owns it: naming a cause is not the same as having a lever. The remaining candidate is a source-shape answer for what makes IDO choose the emission order of two saved-register writes, which is unmeasured.",
    confirmed_on=[
        "kb-sbk1.sqlite attempt 46494 Fstop: statement-order reorder -> exact (cause = order, four DISTINCT stores permuted)",
        "kb-sbk1.sqlite attempt 32870 drawCharacterSelectCoursePreviewPanel8: for-init hoist -> 99.936 to 99.554, the exchanged pair untouched; the naive rule application regressed",
        "ROUTING REFUTATION, 2026-09-17: the five cases (drawRaceSplitscreenSelectOption{2,4}Frame, drawCharacterSelectCoursePreviewPanel{2,6,8}) all at 99.936, sent to regalloc_search with --force-regalloc at budget 300 -> 0 of 5 exact, gradient [0,2,2] -> [0,2,2], best_label baseline. eval/results/colouring-route-20260917/",
        "REORDERING REFUTATION, 2026-09-17: the same five through the ordering pass -> 15 variants predicted, 15 compiled, 0 exact, pair unchanged in every one. patterns/derive.py rule `same-shape-difference-is-a-permutation` reports NOT CONFIRMED. eval/results/rename-wall-20260917/",
    ],
))

register(Pattern(
    id="diff-read-permutation-needs-a-group-discriminator",
    name="A diff-read permutation generalises only as far as the instruction class that identifies the group",
    kind="evidence",
    looks_like="An `order` residual where a run of independent source statements is emitted in a different order than the target's, and the answer is a COMPOSITION of adjacent swaps rather than one swap.",
    means="Measured 2026-09-17 while trying to generalise `patterns/rules.py:StoreOrderRule` past stores. `patterns.ordering.hunk_permutation` correctly computes the permutation a hunk STATES (`order` such that `target == [candidate[i] for i in order]`) -- on a rotation of three independent non-store statements it returns `[0,2,3,1,4,5]`. But a unified diff for a permutation ALWAYS interleaves CONTEXT lines, so the hunk is longer than the statement group: Fstop is 11 hunk lines (8 per side) against a 3-statement run, and the 3-rotation is 5 lines (6 per side) against 3. `StoreOrderRule` works because a STORE carries a discriminator -- its base register -- that identifies which instructions belong to the group, and it filters the function's argument spill out on exactly that basis. A general statement carries no such marker, so 'run length == hunk length' cannot hold on a real residual, and applying a hunk-wide permutation to a shorter run would be a guess about which context lines the run emitted.",
    prescription="Do not generalise a diff-read permutation beyond a class that has an identifying operand; without one the mapping from target positions to source statements is unknowable from the residual. The general transform was written, measured against both cases above, and WITHDRAWN rather than shipped -- a generator whose precondition cannot hold is worse than none, because it sits in the search's family list looking like coverage. `tests/test_statement_permutation.py` asserts the withdrawal so it is not re-added without new evidence. What is kept is `ordering.hunk_permutation` with its declines pinned (several hunks, insert/delete, changed instruction, repeated instruction text), which is the correct statement of what a residual says about order and what a future discriminator would feed.",
    confirmed_on=[
        "hunk vs group size 2026-09-17: Fstop 11 hunk lines / 8 per side against a 3-statement run; the 3-statement rotation 5 hunk lines / 6 per side against 3. `patterns/ordering.hunk_permutation` returns a correct permutation for both, so the failure is the MAPPING, not the computation.",
        "population 2026-09-17, eval/order_class_census.py over the 133 live compiling non-exact functions: 126 not-a-permutation, 5 colouring, 1 order (updateRacePlayerMode16AerialTrick, a LOAD case whose analogous change was already measured to regress 99.712 -> 97.115), 1 no-hunks. There is no queue for a computed permutation.",
        "tests/test_statement_permutation.py: the permutation is read correctly, both motivating cases are asserted, the ambiguous inputs decline, and the withdrawn generator is asserted absent.",
    ],
))

register(Pattern(
    id="declaration-and-order-mutation-search-is-inert",
    name="Blind declaration and statement-order mutation closes nothing, while diff-READ order closes functions",
    kind="review",
    looks_like="A register-only or ordering residual where the search enumerates declaration permutations, single-statement moves or adjacent swaps and the gradient wanders without reaching [0,0,0].",
    means="MEASURED 2026-09-17 over the whole knowledge base (`eval/family_yield.py`): the register search's declaration and order families cost thousands of attempts and closed NOTHING. `local_type` 5,403 attempts / 0 exact; `stmt_move` 5,719 / 0; `stmt_order` 1,586 / 0; `commutative` 3,479 / 1; `decl_order` 1,194 / 1. Twelve of fifteen families have zero exact closures in the corpus. The reference project measured the same thing independently and recorded it in DECOMPILATION_LEARNINGS.md (commits fd27a480, d5dcbb8e): for a symmetric value-kind grouping, declaration order, statement order, per-axis nested scopes, expression grouping, separate carrier variables and source-line layout were ALL inert, because uopt's v0/v1/a0/a1 group is assigned by value KIND rather than source order -- only the order WITHIN each pair follows source. The same note records 65 declaration-order permutations and 183 blank-line layouts with no effect. Against that: reading the target's order OFF THE DIFF and applying it whole closed Fstop (99.999 -> 100.0).",
    prescription="Do not spend search budget on declaration permutations or on blind adjacent-swap enumeration for an allocation-shaped residual. The families are not worthless as GRADIENT steps -- a function usually needs several levers composed, and positional word mismatch is the gradient, so a family can be a necessary stepping stone while never landing the final object; the reference's own imported entry `ido-statement-order-is-an-allocation-lever` shows 24 permutations moving mismatches 271 -> 397 of 422. What is refuted is that they CLOSE anything on their own. Prefer (a) diff-read order where the instruction class carries an identifying operand (see `diff-read-permutation-needs-a-group-discriminator`), and (b) the admission and intake paths, which measure 11-14% per attempt against 0.02% for these families.",
    confirmed_on=[
        "census 2026-09-17, eval/family_yield.py over all 51,095 attempts: local_type 5,403/0, stmt_move 5,719/0, stmt_order 1,586/0, commutative 3,479/1, decl_order 1,194/1; 12 of 15 families with zero exact closures. eval/results/family-yield-20260917/RESULT.md",
        "by strategy root, the same census: faultsearch-d2 2,478 attempts / 2 exact, zero-token-m2c-harvest-m2c 1,726/3, repair-pair 1,260/1 (5,464 attempts for 6 exacts) against campaign-intake 1,158/157 (13.6%) and zero-token-m2c-harvest-typedecl 154/18 (11.7%).",
        "independent corroboration, reference DECOMPILATION_LEARNINGS.md commits fd27a480 and d5dcbb8e, imported as `ido-value-kind-colors-the-argument-group` and `ido-statement-order-is-an-allocation-lever`.",
        "the positive half: Fstop closed exactly by reading the target's emission order off the diff (patterns/catalog.py `ordering-residual-states-the-statement-order`).",
    ],
))

register(Pattern(
    id="split-byte-zero-test-load",
    name="An immediately tested byte load need not remain a named register web",
    kind="solver",
    looks_like="A byte-load loop has an otherwise identical target and candidate instruction stream but the named load temporary occupies a different register.",
    means="A split declaration and immediately tested load can form a named IDO web. In the measured strlen candidate, substituting the same byte-pointer load into its sole zero test changed v0 to target t7 and restored the exact object without changing the loop structure. This establishes one compiler case, not the original source.",
    prescription="Use byte_test_inline.candidates through the bounded isolated-register-web family: require matching explicit byte local and pointer declarations, precisely one assignment and one immediate zero-test read, no intervening operation, no volatile qualifier, local macro directive, shadowing or extra use. Compiler/frontend/semantic/object gates remain authoritative; broader transfer is unmeasured.",
    confirmed_on=[
        "eval/results/small-functions-20260913/tiny-pilot/receipts/strlen-final-generator/summary.json:82748:private-attempt-2",
    ],
))

register(Pattern(
    id="single-local-stack-home-padding",
    name="A leading unused local changes stack-home packing without changing frame size",
    kind="solver",
    looks_like="The entire residual is unambiguously paired lw/sw of one stack word at an eight-byte-aligned target offset versus target+4 in the candidate; opcode/register/order/frame already agree.",
    means="IDO stack-home placement can depend on preceding local allocation even when the total frame is unchanged. In three related ending callbacks, a leading four-byte unused volatile array or eight-byte-aligned union restored the exact object; register qualification or trailing padding did not. This does not establish the original source declaration.",
    prescription="Use rewrites.stack_home_padding_rewrites for one bounded leading-array experiment on a sole uninitialized 32-bit integer local with no explicit address-taking (including parentheses); macro expansion is not analyzed. Decline mixed residuals, ambiguous pairs, multiple homes/locals and existing pad names. Compile/frontend/semantic/exactness gates remain authoritative; broader transfer is unproven.",
    confirmed_on=[
        "eval/results/residual-patterns-20260912/stack-home-v1/report.json:startEndingSlashRepeatAnim:44872",
        "eval/results/residual-patterns-20260912/stack-home-v1/report.json:updateEndingJamPhase3DPrep:44877",
        "eval/results/residual-patterns-20260912/stack-home-v1/report.json:updateEndingJamPhase3FAnim3:44882",
    ],
))

register(Pattern(
    id="named-local-web-across-call",
    name="A declared local living across a call takes its own stack home; inlining it shifts every spill slot",
    kind="solver",
    looks_like="Same frame size; every sp-relative spill/reload around a call sits 4 bytes lower in the candidate than in the target, and the candidate declares a pointer or scalar local initialised once from a call-free value and read at the calls (typically an m2c temp_*).",
    means="IDO gives a declared local that is live across a call a home slot above the compiler's own temporaries; an unnamed expression recomputed or spilled as a temporary does not take that slot. The draft's extra named local, not a pad, is what moves the slots.",
    prescription="Use regalloc_mutations.pure_local_inlines: substitute the value at every read and drop the local, only when the value has no call or side effect and none of its identifiers is written in the body. Object exactness decides; stack_layout pads cannot reach this shape.",
    confirmed_on=["eval/results/restored-holes-20260925/analysis-pop.json:probeControllerPak",
                  "eval/results/restored-holes-20260925/analysis-pop.json:drawMultiplayerRaceHud"],
))

register(Pattern(
    id="named-operand-local-order",
    name="A named operand local flips commutative operand order where a source swap does not",
    kind="solver",
    looks_like="The residual is one commutative instruction with the same registers in swapped order (addu v0,a0,t6 vs addu v0,t6,a0), and swapping the C operands leaves it unchanged.",
    means="IDO orders operands by web, not by source position; hoisting the compound operand into its own named local gives it a separate web and the target order.",
    prescription="Use regalloc_mutations.operand_locals (hoist one parenthesised call-free operand of a simple statement into an s32 local assigned just before it). One confirmation so far; treat transfer as unproven.",
    confirmed_on=["eval/results/restored-holes-20260925/analysis-pop.json:resolveAssetTableRelativePointer"],
))

register(Pattern(
    id="target-call-parameter-byte-units",
    name="Outer pointer casts can hide element-scaled call addresses",
    kind="solver",
    looks_like="A constant offset on an unchanged typed parameter or simple alias is passed through an outer pointer cast; the target call consumes the same root plus that offset in bytes.",
    means="An outer cast does not change the units of the inner pointer addition. Target call values can justify a byte-address candidate even when frontend compilation already passes.",
    prescription="Use address_units.parameter_call_views for o32 source-bound calls. Preserve outer pointer casts, require all target callee occurrences to agree on root/offset, decline ambiguous or mutated roots, and compile every candidate through the normal gates. Arithmetic ratios alone are not type-size evidence.",
    confirmed_on=["eval/results/pointer-units-pilot-20260910-v1/report.json:func_800643B4", "eval/results/pointer-units-pilot-20260910-v1/report.json:updateMenuSpriteActorDebugControls", "eval/results/pointer-units-pilot-20260910-v1/report.json:initThrownTrailImpactProjectile"],
))

register(Pattern(
    id="rom-bound-equivalent-relocation-pairing",
    name="Different intact HI16/LO16 pairings can link to identical function bytes",
    kind="review",
    looks_like="GNU target and IDO candidate have the same function bytes and relocation sites, but different pairings or trailing zero extent.",
    means="Raw object-layout or pairing inequality need not imply different bytes in the bound ROM link environment.",
    prescription="Use function_boundary.certify to validate both extents and resolve each object's intact pairs independently against hashed symbol/ROM inputs. Keep data/BSS, unknown relocations and whole-TU integration outside this certificate; never normalize pairing globally or promote object exactness.",
    confirmed_on=["eval/results/function-boundary-recheck-20260910/report-v2.json:waitEndingTommyPhase39", "tests/test_function_boundary.py:test_each_objects_hi_lo_pairing_is_resolved_independently"],
))

register(Pattern(
    id="o32-loaded-byte-address-call-view",
    name="Header pointer view of a loaded address plus byte displacement",
    kind="solver",
    looks_like="One o32 call argument is a 32-bit load from param0+field plus a constant byte step; draft passes s32 to a header-declared pointer.",
    means="The pointer conversion can follow the entire byte-address expression without introducing pointee-size scaling.",
    prescription="Use frontend_repair's diagnostic/source-bound rule only with one matching header signature, one matching target call, a four-byte load, unchanged first parameter and matching displacement. Recompile with frontend and exact object checks; this is target-specific, not portable integer/pointer equivalence.",
    confirmed_on=["eval/results/frontend-exact-pilot-20260910/func_80064414/general_rule.json"],
))

register(Pattern(
    id="mips3-o32-closed-wide-arithmetic",
    name="Closed MIPS III/o32 arithmetic helpers",
    kind="solver",
    looks_like="Complete four-word argument stores, two 64-bit loads, arithmetic/trap sequence, and two-word return.",
    means="Under the project's explicit -mips3 -32 -O1 recipe, ordinary wide C arithmetic can reproduce these complete helper objects.",
    prescription="Use wide_runtime_interfaces.reconstruct_helper only after full instruction recognition and o32 checks; retain frontend and object certificates. Do not infer signedness from helper names or admit interpreter execution.",
    confirmed_on=["eval/results/resume-blockers-mips3-v3/report.json: " + name for name in (
        "__ull_rshift", "__ull_rem", "__ull_div", "__ll_lshift",
        "__ll_rem", "__ll_div", "__ll_mul", "__ll_rshift")],
))

# The queued community claims were checked individually. These review entries
# preserve measured scope; they deliberately have no automatic hint detector.
# Original guide: https://github.com/n64decomp/oot/blob/master/docs/guides/
# -O2%20decompilation%20(for%20IDO%205.3).md
for _guide_id, _name, _shape, _finding, _advice, _receipt in [
    ("guide-comparison-threshold", "Constant comparison normalization",
     "Signed x > 7 versus x >= 8.",
     "These two spellings produced identical .text under the pinned SBK1 recipe.",
     "Use equivalent threshold spellings as candidates only within the integer type's range; do not generalize across overflow.",
     "queued-v1: comparison-normalization"),
    ("guide-loop-continue-shape", "Continue can change induction-variable shape",
     "An eight-element copy/add loop with or without a trailing continue.",
     "Both loops remained unrolled by four; the counter changed from element units to byte units.",
     "Compare loop stride and bound together. Do not assume continue disables unrolling.",
     "queued-v1: loop-continue"),
    ("guide-array-address-spelling", "Array address spelling is context-dependent",
     "Array-address syntax versus pointer addition.",
     "The simple loop probe was identical. Two compression address edits were inert; the third worsened the score.",
     "Retain both source forms as hypotheses; neither predicts an extra loop counter by itself.",
     "queued-v1: array-address and attempts 29708-29711"),
    ("guide-aggregate-copy-shape", "Aggregate copy can change register choice",
     "Four-int struct assignment versus four explicit member copies.",
     "The probe preserved memory-access order but selected different registers.",
     "Check copy size, padding and alias semantics before proposing aggregate/member rewrites. Reordering is not guaranteed.",
     "queued-v1: struct-copy"),
    ("guide-switch-default-placement", "Switch default source order can affect layout",
     "Three disjoint cases with a breaking default first versus last.",
     "The probe's default body moved and an extra branch appeared; this was a branch chain, not a jump table.",
     "Check case fallthrough and actual residuals before trying label order. A jump-table relocation is a separate issue.",
     "queued-v1: switch-default-order"),
    ("guide-literal-hoisting", "Float literals can survive calls in a saved register",
     "A loop repeatedly passes an extern const float versus a numeric literal to a callee.",
     "The extern object reloaded each iteration; a nontrivial literal loaded from .rodata into f20 before the loop.",
     "Distinguish numeric literals from named const storage. Saved-register hoisting does not explain every f0/f4 mismatch.",
     "supplemental-v2: extern-const and literal-rodata-nontrivial"),
]:
    register(Pattern(id=_guide_id, name=_name, kind="review", looks_like=_shape,
                     means=_finding, prescription=_advice,
                     confirmed_on=["eval/experiments/decompedia/" + _receipt]))

register(Pattern(
    id="guide-branch-likely-cause", name="Branch-likely emission cause remains unconfirmed",
    kind="review", looks_like="A conditional branch has a difficult delay slot.",
    means="The guide labels its explanation tentative. Our probes did not establish a sufficient emission rule.",
    prescription="Check the ISA, conditional branch and actual delay-slot execution. bootThreadMain has no conditional branch, so its unreachable nop residual is not this case.",
))

register(Pattern(
    id="constant-display-list-packets", name="Constant Gfx command pairs in CPU stores",
    kind="review", looks_like="Paired word stores through a loaded display-list buffer pointer.",
    means="Some command words can be recovered without reconstructing the entire CPU function.",
    prescription="Use tools.gfx_packet_audit with an explicit buffer symbol and microcode. Keep dynamic words unknown; verify decoded macro bytes with the target GBI header.",
    confirmed_on=["eval/experiments/decompedia/graphics-v2/receipt.json: two DEV graphics functions, 39 candidate pairs, 18 macro instances round-trip to the target-derived packet bytes; 20 dynamic pairs and one raw constant packet remain unresolved."],
))

register(Pattern(
    id="prototype-spelling-not-fixed-frame-delta",
    name="Prototype spelling does not impose a fixed stack-frame delta",
    kind="review",
    looks_like="A frame mismatch invites changing a no-argument definition "
               "between (void) and ().",
    means="The old guide's four-byte rule is not universal under the installed "
          "SBK1 compiler recipe. Naming a temporary also need not add a slot.",
    prescription="Measure each source change with the actual compiler and "
                 "frontend. Do not weaken the prototype policy for byte equality.",
    confirmed_on=[
        "eval/experiments/decompedia/results-v1/receipt.json: five paired "
        "synthetic probes have identical .text and frame sizes; DEV "
        "fadeInEndingCreditsFlow attempts 29694/29695 are both object-exact, "
        "but the empty parameter list fails strict-prototypes."
    ],
))

register(Pattern(
    id="call-count-decides-spill-vs-saved-register",
    name="Few calls crossed spill to the stack; more calls buy callee-saved registers",
    kind="review",
    looks_like="Values live across a call reloaded with `lw tN,off(sp)` after the jal and no "
               "`sw sN,off(sp)` in the prologue -- or the reverse, s-registers saved with no spills.",
    means="Under the SBK1 -O2 -mips1 recipe, two values live across 1 or 2 calls are spilled to "
          "stack slots; at 3 calls one s-register is saved, at 4 two. Register choice follows how "
          "many calls a value crosses, not merely whether it crosses one.",
    prescription="Do not read a missing s-register as a wrong local or a missing variable. Before "
                 "adding or removing a call-crossing local to chase saved registers, count the calls "
                 "it crosses. Probed for two live values only; other counts are unmeasured.",
    confirmed_on=[
        "tools/synthetic_corpus.py probe 2026-09-16: two locals across 1/2/3/4 calls saved "
        "none/none/s0/s0+s1",
        "eval/results/synthetic-corpus-20260916/pairs.receipt.json: stack_spill 150/150 spill with "
        "no s-register at 1 call; saved_regs 150/150 use one or more s-registers at 3-6 calls",
        "tests/test_synthetic_corpus.py:test_features_separate_stack_spills_from_saved_registers",
    ],
))

register(Pattern(
    id="independent-relocation-group-order",
    name="Independent relocation groups differ in table order, not meaning",
    kind="review",
    looks_like="Identical allocated section bytes/layout, with the same external "
               "scalar relocations or intact HI16/LO16 pairs in different orders.",
    means="GNU-as and IDO can serialize disjoint relocation groups differently. "
          "This is not a source-code mismatch. HI16/LO16 pairing itself remains significant.",
    prescription="Compare disjoint complete groups in solver.byte_certificate, never "
                 "sort individual relocation entries. Decline overlapping writes, orphan "
                 "LO16s and multi-HI extensions. Require isolated full-ROM integration. "
                 "ABI rationale: https://sourceware.org/pipermail/binutils/2023-February/125959.html",
    confirmed_on=[
        "SBK1 initCharacterSelectCourseStatsBadge: identical 64-byte text and intact "
        "callback HI16/LO16 pair listed before/after independent jal; autonomy-wavefront-24-v2",
        "SBK1 initCharacterSelectCoursePreviewPanel6: identical 80-byte text, same "
        "independent callback pair/jal ordering difference; autonomy-wavefront-24-v2",
        "SBK1 waitRaceIntroFlyoverShortPanFinal: identical 80-byte text and relocation "
        "groups, callback pair/jal permutation; autonomy-wavefront-24-v2",
    ],
))

register(Pattern(
    id="bulk-struct-copy",
    name="Struct assignment compiled to a word-wise copy run",
    kind="evidence",
    looks_like="Three or more width-4 accesses, same direction, same base, at "
               "stride-4 consecutive offsets, interleaved load/store.",
    means="A whole-struct assignment. The words are a memcpy, not field accesses.",
    prescription="Exclude these accesses from field-width inference. They carry "
                 "no information about the layout they happen to traverse.",
    confirmed_on=[
        "SBK1 RaceUiPodiumTrailActor.copyBlock (Transform3D at 0x24, first "
        "member is s16, copied word-wise at 0x34/0x38/0x3C while a genuine "
        "lh reads 0x24)",
    ],
))

register(Pattern(
    id="uopt53-derived-iv-from-counter",
    name="uopt derives a counter loop's offset and index variables itself; hand-written derived variables differ",
    kind="solver",
    looks_like="Target and candidate differ only in the order of the zero initialisations of a loop's variables "
               "(`move s2,zero; move s3,zero` swapped), and the loop bound sits in the register of an earlier loop's "
               "counter. The source steps `offset += K; idx++;` itself and tests `offset != B`.",
    means="`for (i = 0; i < N; i++) { ... i * K ... a[i] ... }` is strength-reduced by uopt into derived variables "
          "(stepped by K and by the element size) with the exit test moved onto the offset (`bne off, bound`, bound "
          "0xK*N hoisted). Written out by hand, the same loop compiles differently (H6 P6a); `i << s` is not `i * K` "
          "(P6b, the shift stays in the loop); the test moves onto the derived offset (P6c). A no-op `i++; i--;` after "
          "the loop keeps the counter live and blocks the match. 25 earlier compiles reordering the initialisations "
          "never moved the order (register-steer-20260923).",
    prescription="Rewrite derived variables stepped at a bottom-tested loop's tail as a counter `for` loop, and a "
                 "variable stepped in lockstep with a `for` counter as that counter (strength_inverse."
                 "counter_loop_variants, family `counter_loop`). The five 99.936 siblings: 100.0.",
    confirmed_on=["syn H6 P6a-P6c (3/3)", "drawCharacterSelectCoursePreviewPanel2/6/8, "
                  "drawRaceSplitscreenSelectOption2/4Frame fire tests"],
))

register(Pattern(
    id="m2c-var-at-is-a-branch-temp",
    name="m2c's `var_at` is a comparison IDO evaluated straight into a branch; never a C variable",
    kind="solver",
    looks_like="Target `slt at,x,y; bnez at` where the candidate has `slt a3,x,y; bnez a3`; the source carries m2c's "
                "`s32 var_at; ... var_at = x < y; ... while (var_at != 0)` (often assigned on several paths).",
    means="`at` is the assembler temporary: IDO uses it for compare-and-branch temporaries and address halves and never "
          "homes a C variable there. m2c names a variable after the register, so a comparison the compiler "
          "duplicated onto several paths becomes a real variable with its own register. Paths where m2c "
          "constant-propagated it (`var_v0 = 4; var_at = 4 < -4;`) are the same comparison.",
    prescription="Fold every `var_at` back into the test that reads it (solver.branch_shape.at_inline). Fire tests: "
                 "MusStop, MusHandleSetFreqOffset, MusHandleSetPan, MusHandleSetVolume 100.0; MusHandleStop "
                 "82.6 -> 98.6.",
    confirmed_on=["MusStop, MusHandleSetFreqOffset, MusHandleSetPan, MusHandleSetVolume fire tests"],
))

register(Pattern(
    id="ido53-struct-copy-loop",
    name="IDO 5.3 lowers a large struct assignment to its own 12-byte copy loop; a C word loop differs",
    kind="solver",
    looks_like="`addiu END,SRC,N-4`, then a loop of three `lw at`/`sw at` pairs stepping both pointers by 12, "
               "`bne SRC,END`, and a one-word tail; the candidate has m2c's `M2C_MEMCPY_ALIGNED(D, S, N)` as a call.",
    means="A whole-struct assignment of N bytes (16 words tested). A `for` word loop over the same words compiles "
          "to a different, 4x-unrolled counted loop (E4, eval/results/branch-layout-20260924).",
    prescription="Write m2c's pseudo-call as `*(T *)D = *(T *)S;` with `typedef struct { s32 w[N/4]; } T;` "
                 "(solver.branch_shape.m2c_struct_copy). copyGfxCommandBlockToScratch 42.0 -> 100.0.",
    confirmed_on=["syn E4 struct_assign vs word_loop", "copyGfxCommandBlockToScratch fire test"],
))

register(Pattern(
    id="ido53-unaligned-struct-copy",
    name="IDO 5.3 copies an alignment-1 struct with lwl/lwr; the stores stay `sw` only into a declared local",
    kind="solver",
    looks_like="Target `lwl at,0(S)`/`lwr at,3(S)` + `sw at,0(D)` word copies. At 8 bytes: two straight-line pairs. "
               "At 40 bytes: `addiu END,S,0x24` and a loop of three pairs stepping both pointers by 12, then a trailing "
               "word. The candidate has m2c's transcription with each word lowered to four `lbu` packed by "
               "`sll 24/16/8` + `or`.",
    means="Assignment of an all-`u8` struct (alignment 1) of N bytes. The source is read unaligned; the destination is "
          "stored with an aligned `sw` only when it is a declared local of that struct type. Written through a cast "
          "pointer (`*(T *)arr = ...`) or a union member, the stores become `swl/swr` as well. The byte-packed "
          "form compiles to 0 lwl/24 lbu/18 sll at -O2. Probed at -O1 and -O2 (eval/results/unaligned-copy-20260929: "
          "copy.c, copy4.c, castdst.c, aligndst.c, packed.c).",
    prescription="Replace the copy with `DST = *(T *)(SRC);`, keeping DST's struct type or retyping an array DST to "
                 "`typedef struct { u8 bytes[N]; } UnalignedN;` (solver.unaligned_copy). Fire run on the best states: "
                 "6 of 8 eligible fire, 6/6 compile with matching lwl/lwr counts; osMotorStart 50.6 -> 89.3, "
                 "__osContRamWrite 52.2 -> 84.2, __osContGetInitData 49.1 -> 78.2, osContGetReadData 24.0 -> 57.9.",
    confirmed_on=["IDO probe copy/castdst/aligndst at -O1 and -O2", "osMotorStart, osMotorStop, __osContRamRead, "
                  "__osContRamWrite, __osContGetInitData, osContGetReadData fire runs"],
))

register(Pattern(
    id="ido53-o1-copyback-temporary",
    name="IDO 5.3 -O1: m2c's copy-back temporary gets a stack home the direct assignment doesn't",
    kind="solver",
    looks_like="-O1 candidate `addiu t8,t7,1; sw t8,H(sp); ... lw t1,H(sp); slt at,t1,..` where the target keeps the "
               "value in its register (`addiu t5,t4,1; sw t5,X(sp); ... slt at,t5,..`), and a larger frame. The source "
               "has m2c's `temp_tN = X + 1; X = temp_tN; ... if (!(temp_tN < n)) break;`.",
    means="At -O1 every local has a home, so a temporary that only re-names X is stored and reloaded; `X = X + 1` "
          "compares straight from the register (eval/results/temp-copyback-20260929: m2c.c vs direct.c vs forloop.c).",
    prescription="Merge `T = E; X = T;` into `X = E;` and read X where T was read, when T is not read before the pair and "
                 "every later read precedes the next write of X or T (solver.temp_copyback). Pending population measurement.",
    confirmed_on=["IDO -O1 probe m2c/direct/forloop"],
))

register(Pattern(
    id="ido53-o1-array-element-base",
    name="IDO 5.3 -O1: a constant-index array element of a local is loaded off a computed base; a named field off sp",
    kind="solver",
    looks_like="Candidate `addiu tN,sp,OFF; lbu tM,K(tN)` (and knock-on `andi s0,v0,0xff`, an extra s-register save and "
               "a larger frame) where the target has `lbu tM,OFF+K(sp)`.",
    means="`l.bytes[38]` on a local struct costs an address register at -O1; `l.datacrc` (a named field at the same "
          "offset) is addressed directly off sp. Probe: field_array.c 80-byte frame + andi s0; field_named.c 64-byte "
          "frame, direct lbu (eval/results/unaligned-copy-20260929).",
    prescription="Give generated byte structs named fields (b0..bN-1) and turn constant indexes into field accesses "
                 "(solver.unaligned_copy). osMotorStart's copy step went 89.3 -> 93.8 with frame and width resolved.",
    confirmed_on=["IDO -O1 probe field_array/field_named", "osMotorStart copy variant"],
))

register(Pattern(
    id="ido53-o1-counted-loop-shape",
    name="m2c's rotated counted loop and the `for` it came from compile to different temporary orders",
    kind="solver",
    looks_like="m2c `i = 0; if (n > 0) { for (;;) { ...; i += 1 (or temp_tN = i + 1; ... i = temp_tN); "
               "if (!(i < n)) break; } }`, register-only residue in the loop (the target's counter in t4 where the "
               "candidate's is in t2).",
    means="Same control flow and instruction count, but IDO assigns ugen temporaries in a different order for the "
          "rotated spelling. On osMotorStart at 99.223 (registers only), `for (i = 0; i < n; i++)` gave 100.0; the "
          "swapped statements, `++` forms, a comma step and an unguarded for gave 94.7-99.4 "
          "(eval/results/temp-copyback-20260929/loopshape.py).",
    prescription="Write the loop back as `for (i = 0; i < n; i++)` when nothing after the increment reads the counter "
                 "(solver.counted_loop). The greedy chain rebuilt osMotorStart and osMotorStop exact from their raw drafts.",
    confirmed_on=["osMotorStart loopshape", "osMotorStop loopshape", "greedy_chain osMotorStart/osMotorStop exact"],
))

register(Pattern(
    id="signedness-cast",
    name="Same location loaded both signed and unsigned",
    kind="review",
    looks_like="lh and lhu (or lb/lbu) at one base+offset.",
    means="An ordinary C cast, e.g. reading an s16 field through (u16). The "
          "field has one type; the reads differ.",
    prescription="Do NOT treat as a type conflict. Report as a note. Prefer the "
                 "declared width; signedness needs other evidence.",
    confirmed_on=["SBK1: fires on 8 locations across a 100%-matched codebase"],
))

register(Pattern(
    id="union-polymorphic-offset",
    name="One offset legitimately carries several widths",
    kind="evidence",
    looks_like="Conflicting widths at a single base+offset that survive "
               "bulk-copy filtering.",
    means="A union, or a padding array overlaying named fields.",
    prescription="Model acceptable widths as a SET, never a single value. A "
                 "width outside the set is a finding; one inside is not.",
    confirmed_on=[
        "SBK1 RaceUiDualCounterActor@0x18 (s8 row / s16 alpha18)",
        "SBK1 CourseSelectWidgetActor@0x1c (u8 pad18[4] / s16 spriteIndex)",
    ],
))

register(Pattern(
    id="ra-as-scratch",
    name="$ra allocated as a general-purpose register",
    kind="evidence",
    looks_like="Loads and stores based on $ra in a non-leaf function that has "
               "already spilled ra to the stack.",
    means="IDO's allocator reusing ra once it is safely saved. Nothing to do "
          "with return addresses.",
    prescription="Treat $ra like any other register when resolving bases. Do "
                 "not special-case or discard it.",
    confirmed_on=["SBK1 drawMenuSpriteWithPaletteScale (909 ra-based accesses "
                  "across the game)"],
))

register(Pattern(
    id="hi-lo-address-pair",
    name="lui/addiu or lui/lo-offset global addressing",
    kind="evidence",
    looks_like="lui rX, %hi  then  addiu rX, rX, %lo  (or the lo folded into a "
               "load's own immediate).",
    means="A absolute address of a global. The addiu form yields a pointer that "
          "is then field-indexed; the folded form addresses the symbol directly.",
    prescription="Resolve to global:0xADDR. In the folded form the field offset "
                 "is zero, because the immediate IS the low half of the address.",
    confirmed_on=["SBK1: 9,564 resolved global accesses across 1,259 functions"],
))


# ------------------------------------------------------ hypotheses (unconfirmed)

register(Pattern(
    id="saved-colour-follows-adjsave-and-locks-emission-order",
    name="uopt numbers saved ranges by descending adjsave, and in two measured compiles emits same-block definitions in the reverse of that ranking",
    kind="review",
    looks_like="Two saved-register definitions with the same shape in one block, e.g. `move s2,zero` / `move s3,zero`: whichever value uopt weighted higher takes the LOWER saved register, and whichever took the HIGHER saved register is emitted FIRST. Register assignment and emission order move together on every source form measured, so a target that needs the first-emitted value in the lower register needs either a different unit of ordering or a different weighting.",
    means="HYPOTHESIS, NOT CONFIRMED -- do not route on it. Measured 2026-09-17 by reading uopt's own `-zdbug:5/6` trace for the two ends of the known path on drawRaceSplitscreenSelectOption2Frame, with `eval/results/rename-wall-20260917/adjsave_probe.py`. FIRST PART, well established: colour assignment is strictly descending adjsave in both compiles (baseline decision order adjsave [26.0, 18.6, 17.0, 14.4, 6.2, 6.2, 3.3, 1.0]), reproducing the 2026-09-14 census (1,907/1,976 procedures) on cases it did not cover. SECOND PART, TWO DATA POINTS ONLY: the two values in question are the tile index and the offset, identified by which `= 0` statement each edit changes, and their adjsave rank swaps between the two compiles exactly as their registers do -- baseline tileIndex 14.4 -> colour 17 (s3), offset 17.0 -> colour 16 (s2), emission tileIndex then offset; best tileIndex 18.0 -> colour 16 (s2), offset 17.0 -> colour 17 (s3), emission offset then tileIndex. In both, the first-emitted value is the one with the LOWER adjsave. Two compiles cannot separate 'emission order IS the reverse of the adjsave ranking' from 'emission order follows ugen's u-code order and merely coincided twice'.",
    prescription="Treat this as the current best explanation of the coupling in `saved-order-has-a-gradient-and-no-generator`, not as a lever. It names TWO candidate levers and does not choose between them, and each is falsifiable: if emission order is the reverse of the adjsave ranking, the TARGET's two zero-initialisations must be in DIFFERENT basic blocks, because the target emits the tile index first AND holds it in s2, which would need its adjsave to be both below and above the offset's; if instead the u-code order is independent, a source shape exists that flips the order of the two definitions while leaving the adjsave ranking alone, and the ten source orderings already measured say it is not a statement permutation. Testing the first needs the target's own u-code, which the tracing toolchain can only produce from a source we can compile -- a MATCHED function with the same shape would do it. Until then: do not build an emission-order or adjsave-driven mutation family on two compiles. What the measurement DOES establish is that the `saved_order` swap is not a statement-order question at all -- it is the save pass's weighting and the definition order, which is a different instrument from both `statement_order_rewrites` and `regalloc_mutations`.",
    confirmed_on=[],
))


# --------------------------------------------- mined from the matched corpus

register(Pattern(
    id="s16-sign-extend",
    name="sll 16 / sra 16 pair",
    kind="solver",
    looks_like="sll rX, rY, 0x10  then  sra rZ, rX, 0x10. Frequently interleaved "
               "in pairs when two values are promoted together.",
    means="Sign-extension of a 16-bit value to 32 bits -- an s16 variable used "
          "in 32-bit arithmetic.",
    prescription="Declare the variable s16, not s32. The pair disappears if the "
                 "source type is already 32-bit, so its presence is direct "
                 "evidence about a declaration.",
    confirmed_on=["SBK1 drawMenuTilemapSprite @ 0x80011f20 "
                  "(sll t8,t4,0x10 / sra t4,t8,0x10); 120 occurrences"],
))

register(Pattern(
    id="signed-div-power-of-2",
    name="Bias-corrected arithmetic shift",
    kind="solver",
    looks_like="bgez rX, +8 ; sra rD, rX, N ; addiu rT, rX, (2^N - 1) ; "
               "sra rD, rT, N. Often preceded by multu/mflo.",
    means="Signed division by 2^N. The bias on the negative path implements "
          "round-toward-zero, which a bare shift does not.",
    prescription="Write `x / 4096`, NOT `x >> 12`. The two differ for negative "
                 "operands and the shift will never match. When preceded by "
                 "multu/mflo it is fixed-point multiply: `(a * b) / 4096`.",
    confirmed_on=["SBK1 updateMainMenuSceneModelTransforms @ 0x80042260 "
                  "(multu s6,t8 / mflo t9 / bgez / sra 0xc / addiu 4095 / sra 0xc); "
                  "28 occurrences"],
))

register(Pattern(
    id="data-decoded-as-code",
    name="Data symbols disassembled as instructions",
    kind="evidence",
    looks_like="Implausible opcode runs (`mfhi mfhi mfhi mfhi`, long `sra` "
               "chains) under symbols named D_*, jtbl_*, or g*Table.",
    means="objdump labelling a data object inside an executable section and "
          "decoding its bytes. Not code at all.",
    prescription="Filter to STT_FUNC symbols via `objdump -t` before extracting "
                 "anything. Noise shaped like evidence is the one thing the "
                 "evidence tier must never contain.",
    confirmed_on=["SBK1: 629 of 2,742 disassembled symbols were data, "
                  "producing 4,561 fabricated mem_access rows (6.7% of the tier). "
                  "Surfaced by patterns/mine.py, not by the golden test -- the "
                  "garbage landed in uncheckable buckets."],
))


register(Pattern(
    id="repeated-store-same-address",
    name="Two stores to one address in a row",
    kind="solver",
    looks_like="`sh rA, 0(rV)` then `sh rB, 0(rV)` -- the same base and offset "
               "stored twice with no intervening load.",
    means="Two SEPARATE C statements assigning to the same variable. IDO does "
          "not coalesce them, so each assignment emits its own store.",
    prescription="Each statement must READ THE VARIABLE BACK, so use compound "
                 "assignment on the variable itself: `x++;` then `x &= 0xFF;`. "
                 "Two statements alone are NOT enough -- if the second uses a "
                 "precomputed local (`x = n; x = n & 0xFF;`) the first store is "
                 "dead and IDO deletes it, emitting one store instead of two. "
                 "The read-back is what keeps the first store live.",
    confirmed_on=[
        "SBK1 randomNextSecondary: target stores twice to gSecondaryRngIndex; "
        "source is `gSecondaryRngIndex++;` then `gSecondaryRngIndex &= 0xFF;`",
        "Negative confirmation: prompted to write two statements, the model "
        "produced `gSecondaryRngIndex = next; gSecondaryRngIndex = next & 0xFF;` "
        "and IDO emitted ONE sh -- dead-store elimination. 87.92%, not a match.",
    ],
))

register(Pattern(
    id="register-mirroring",
    name="Locals named after registers instead of idiomatic C",
    kind="review",
    looks_like="Candidate C declaring u16 t6, t8, t9 and assigning through them "
               "step by step, mirroring the assembly's register sequence.",
    means="The model is transcribing registers rather than recovering source. "
          "Original authors wrote compound assignments on real variables; the "
          "register sequence is the COMPILER's output, not the input.",
    prescription="Prefer direct operations on the actual globals, parameters and "
                 "struct fields. Introduce a local only where one is needed to "
                 "reproduce ordering. Register-shaped C rarely matches, because "
                 "the compiler chose those registers for source it never saw.",
    confirmed_on=["SBK1 randomNextSecondary: model produced 5 register-named "
                  "locals for a 3-line function and plateaued at 86.25%"],
))


register(Pattern(
    id="relocation-mismatch",
    name="Instructions identical, object still not byte-exact",
    kind="solver",
    looks_like="`Score: 99.999% (0 differences)` together with "
               "`Verified exact match: no`. The normalized instruction diff is "
               "empty and the object still differs.",
    means="The relocations differ, not the code. A symbol is referenced by the "
          "wrong name, or through a different expression that yields the same "
          "instructions but a different relocation entry.",
    prescription="Stop tuning the code -- it is already right. Check symbol "
                 "references instead: `arr` vs `&arr[0]`, a local extern that "
                 "should be the project's declaration, a differently-typed "
                 "array. Compare relocation tables, not the instruction diff, "
                 "because the instruction diff cannot see this.",
    confirmed_on=["SBK1 initEndingCreditsCharacterLoopingSparkle: two of three "
                  "draws reached 0 instruction differences and still reported "
                  "'Verified exact match: no'"],
))

register(Pattern(
    id="isolated-register-web-source-shape",
    name="One connected temporary web uses different registers",
    kind="solver",
    looks_like="Target and candidate have equal instruction and text lengths, "
               "matching opcodes/immediates/relocations, and a residual "
               "confined to register operands in one short def-use web.",
    means="The semantics and broad expression graph may already agree, while "
          "temporary type, lifetime, declaration order, expression grouping, "
          "or coalescing changes IDO's register coloring.",
    prescription="Preserve control flow and instruction selection. Search a "
                 "small semantics-preserving family: make the old/new/masked "
                 "values explicit or implicit, vary their justified C widths "
                 "and signedness, reorder declarations, and regroup the "
                 "expression. Do not assume C variable names select registers.",
    confirmed_on=[
        "SBK1 randomNextObject: a 2-register-fault residual became verified "
        "exact when `u8 idx=field; idx++; field=idx; return table[field]` "
        "was regrouped as `field++; return table[field]` (attempt 27917; "
        "80/80 semantic cases passed)",
        "SBK1 fixedCosine: reusing the normalized angle parameter and naming "
        "the complete s16 table/shift result reached verified exactness "
        "(attempt 27979; 80/80 semantic cases). Naming only the table load "
        "stopped at 99.167; completing the sibling panel exposed the match.",
    ],
))

register(Pattern(
    id="early-byte-load-return-postincrement",
    name="Early byte load and returned pointer share a postincrement",
    kind="solver",
    looks_like="A byte-pointer input is loaded, two disjoint fields are "
               "written, and the input pointer plus one is returned. The "
               "target loads before the first potentially aliasing write.",
    means="Pointer aliasing can expose a real read/write ordering defect "
          "despite complete branch coverage. After restoring the early read, "
          "postincrement source grouping can also recover the allocation.",
    prescription="Replay aliases into the target's written fields. Propose "
                 "an early RHS temporary, then a direct destination store "
                 "using *input++ followed by the disjoint write and return "
                 "input. Compile and replay every candidate; alias ordering "
                 "is behavioral and cannot be inferred from field names alone.",
    confirmed_on=[
        "SBK1 Fendit: alias-aware root 65/70; early read repaired semantics; "
        "inlined postincrement store reached exact attempt 27959, 70/70.",
        "SBK1 Fvelocity: transferred early-read/postincrement family reached "
        "exact attempt 27965, 70/70, without a model call.",
    ],
))

register(Pattern(
    id="signed-comparison-type-family",
    name="slt target emitted as sltu by a C89 type family",
    kind="solver",
    looks_like="The residual pairs `slt` with `sltu` using identical register "
               "operands; the source compares a u32 value to a high-bit "
               "32-bit literal.",
    means="Signedness is wrong in more than one place. In C89 a literal such "
          "as 0xFFCE0000 is unsigned, so changing only the field to s32 still "
          "emits sltu. The field, related call prototype, and every clamp "
          "literal may need to change as one type family.",
    prescription="Propose both surgical edits and one bounded atomic family: "
                 "u32 declarations/prototype positions to s32, and high-bit "
                 "literals to their negative two's-complement spelling. "
                 "Compile every proposal; these edits can change semantics.",
    confirmed_on=[
        "SBK1 updateEndingSlashSlideRightToMarker: field-only and literal-only "
        "edits stayed non-exact; the six-u32/two-literal family plus the "
        "callback relocation reproduced all 31 instructions exactly in a "
        "12-compile replay",
    ],
))

register(Pattern(
    id="pointer-table-slot-address",
    name="Address of a pointer-table slot instead of its stored pointer",
    kind="solver",
    looks_like="Target has `lw r,%lo(table)(base)` after indexed address "
               "formation; candidate materializes `%lo(table)` with addiu and "
               "has no corresponding load.",
    means="The candidate wrote `&table[index]`, computing the slot address. "
          "The target wrote `table[index]`, loading the pointer stored there.",
    prescription="When the relocation symbol agrees and source contains the "
                 "exact `&table[index]` spelling, propose removing only `&`; "
                 "the oracle verifies the inferred dereference.",
    confirmed_on=[
        "SBK1 hasPendingRaceReplayCourseGridEntry: removing & added the target "
        "relocation-bearing lw and raised the candidate from 88.250% to "
        "99.000%",
    ],
))

register(Pattern(
    id="explicit-branch-sentinel-local",
    name="Explicit signed sentinel local controls symmetric beq operand order",
    kind="solver",
    looks_like="Target and candidate have the same beq/bne and branch target, "
               "but the first two registers are reversed. Mirroring a literal "
               "comparison in C recompiles to the same candidate order.",
    means="IDO canonicalizes literal comparisons, so `-2 != status` is not a "
          "strong enough source lever. Assigning -2 to an explicit s16 local "
          "and comparing `sentinel != status` creates a distinct live web and "
          "preserves sentinel-register-first encoding.",
    prescription="For simple non-zero equality sentinels, declare C89 locals, "
                 "assign them before the enclosing loop/comparison, place the "
                 "sentinel on the left, and try both declaration orders. "
                 "Use only after the semantics-preserving comparison mirror "
                 "fails; verify every variant with the oracle.",
    confirmed_on=[
        "SBK1 hasPendingRaceReplayCourseGridEntry: constant-left source stayed "
        "at 99.000%; two explicit s16 locals ordered -1 then -2 changed only "
        "the two beq operands and produced a 21-instruction exact match",
    ],
))

register(Pattern(
    id="prior-result-in-call-delay-slot",
    name="A prior result is stored in the next call's delay slot",
    kind="solver",
    looks_like="`jal laterFunction` immediately followed by `sb/sh/sw v0, "
               "offset(base)` in its delay slot.",
    means="The store executes BEFORE laterFunction. Therefore v0 cannot be "
          "that function's return value; it is a result kept live from an "
          "earlier call or computation while the later call is prepared.",
    prescription="Trace v0 backward across the call setup. Assign the field "
                 "from the earlier producer, then call the later function as "
                 "a separate statement. Correct both extern return types. Do "
                 "not write `field = laterFunction(...)`—that forces a "
                 "post-call reload/store and cannot match the delay slot.",
    confirmed_on=[
        "SBK1 initTimeTrialRecordDeltaPopup: v0 returned by "
        "calculateRaceTimerDelta is stored to actor+0x30 in the delay slot of "
        "setCallbackTaskCallback; reassigning result ownership plus the "
        "layout/order fixes produced all 30 instructions exactly",
    ],
))


register(Pattern(
    id="store-then-reread-global-keeps-its-address",
    name="A global stored and then read back keeps its address in a register",
    kind="solver",
    looks_like="`lui rN,%hi(g) / addiu rN,rN,%lo(g) / sw v0,0(rN)` where a plain `g = v` compiles to "
               "`lui at,%hi(g) / sw v0,%lo(g)(at)`; later accesses go through v0, not a reload of g.",
    means="The source stored a value into the global and then used the GLOBAL (not the temporary) in a "
          "later expression. IDO forwards the stored value, so no reload appears, but the address of g was "
          "already materialised for reuse, so the store addresses through the register at offset 0. "
          "Declaration shape (scalar, array, struct, unknown-size array) and store spelling (index 0, "
          "pointer temp, volatile, reordering) do not produce it.",
    prescription="solver/site_edits `readback`: after `G = t;` replace the first (or every) later use of t "
                 "with (G). SUPERSEDED for the spawnEndingCredits* shape by "
                 "`copy-from-stored-global-keeps-address-in-a0`: the re-read is a real `lw` in ugen's output "
                 "(`cc -S`) that as1 copy-propagates away, so it spends a ugen temporary (t8 where the target "
                 "has t7) and the address lands in v1, not a0. Keep it as a lower-priority operator; it closed "
                 "updateRaceCamera.",
    confirmed_on=[
        "2026-09-29 IDO 5.3 -O2 -mips1 -G 0 standalone probe (scratch idoprobe/p.sh and q.sh): six "
        "declaration shapes all folded to %lo; `t = call(); gT = t; *(s16 *)((char *)gT + 0x18) = a0;` "
        "produced lui/lh/addiu/sw v0,0(v1)/sh, the target's instruction shape.",
        "2026-09-29 spawnEndingCreditsSmallBurst and spawnEndingCreditsCharacterAura (retained campaign "
        "sources): re-reading gActiveMenuTask for the first field store turned the store residual into a "
        "register-only one (v1 vs a0), score 84.5 -> 98.5 and 88.9 -> 98.2. No reference C was used.",
    ],
))


register(Pattern(
    id="copy-from-stored-global-keeps-address-in-a0",
    name="Copy the temporary FROM the stored global, not the global from the temporary",
    kind="solver",
    looks_like="After a call: `lui a0,%hi(g) / (unrelated) / addiu a0,a0,%lo(g) / sw v0,0(a0)`, later field "
               "stores through v0, ugen temporaries in unbroken order (t6, t7). `t = call(); g = t;` gives "
               "`lui at / sw v0,%lo(g)(at)` instead.",
    means="The source was `g = call(); t = g;` (or `t = g = call();`). In ugen's output the address of g is "
          "materialised with `la` into an argument register and the copy back into t is forwarded, so no "
          "extra temporary appears. Where the address register lands depends on the allocator (a0 in every "
          "case probed so far), but the copy direction is what makes it a register at all. 56 spellings of "
          "the other direction (types, casts, re-reads, temps, chaining the wrong way) never produced it.",
    prescription="solver/site_edits `copydir`: rewrite `t = e; ... G = t;` as `G = e; t = G;` when nothing "
                 "between them calls, reads t, or writes what e reads. The register protocol flagged this "
                 "family's residual as unreachable through register edits, which pointed the probe here.",
    confirmed_on=[
        "2026-09-29 IDO 5.3 -O2 standalone (scratch idoprobe/invisible.py): `t = gT = call` and "
        "`gT = call; t = gT` both reproduce the target sequence exactly; nine other shapes fold to %lo.",
        "2026-09-29 spawnEndingCreditsCharacterAura, ...CharacterVanishPoof, ...DelayedSparkle, "
        "...PhaseAdvanceSparkle, ...SmallBurst: campaign-retained sources with the one statement rewritten "
        "certify byte-exact with frontend passing (scratch reverse_copy.py). No reference C was used.",
    ],
))


# --------------------------------------------------------------- detectors
#
# A detector reads TARGET assembly and says whether this pattern is present.
# That matters because best-of-N beat sequential refinement: guidance has to
# reach the FIRST prompt, so it must be selectable from the target alone, with
# no attempt to diff against yet.
#
# Detectors are deliberately conservative. A hint that fires wrongly is worse
# than one that never fires -- it spends prompt budget telling the model to do
# something the target does not ask for.

_STORE_RE = re.compile(r"^\s*(?:/\*.*?\*/)?\s*(s[bhw])\s+\$?\w+,\s*(-?(?:0x)?[0-9a-fA-F]+)\(\$?(\w+)\)",
                       re.MULTILINE)


def detect_repeated_store(asm: str) -> bool:
    """Two stores to one base+offset with no intervening load of it."""
    seen: dict[tuple[str, str], int] = {}
    for op, off, base in _STORE_RE.findall(asm):
        key = (base, off)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= 2:
            return True
    return False


def detect_s16_sign_extend(asm: str) -> bool:
    return bool(re.search(r"sll\s+\$?\w+,\s*\$?\w+,\s*0x10\b", asm) and
                re.search(r"sra\s+\$?\w+,\s*\$?\w+,\s*0x10\b", asm))


def detect_signed_div(asm: str) -> bool:
    return bool(re.search(r"\bbgez\b", asm) and re.search(r"\bsra\b", asm)
                and re.search(r"addiu\s+\$?\w+,\s*\$?\w+,\s*(?:0x)?[0-9a-fA-F]*(?:7ff|fff|1ff|3ff)\b",
                              asm, re.IGNORECASE))


_CALL_DELAY_V0_STORE = re.compile(
    r"(?m)^\s*(?:/\*.*?\*/\s*)?jal\s+[^\n]+\n"
    r"\s*(?:/\*.*?\*/\s*)?s[bhw]\s+\$?v0\s*,")


def detect_prior_result_in_call_delay_slot(asm: str) -> bool:
    return bool(_CALL_DELAY_V0_STORE.search(asm))


CATALOG["repeated-store-same-address"].detector = detect_repeated_store
CATALOG["s16-sign-extend"].detector = detect_s16_sign_extend
CATALOG["signed-div-power-of-2"].detector = detect_signed_div
CATALOG["prior-result-in-call-delay-slot"].detector = \
    detect_prior_result_in_call_delay_slot


register(Pattern(
    id="narrow-param-homing",
    name="Dead argument store plus andi at function entry",
    kind="solver",
    looks_like="`sw $aN, K($sp)` near entry whose stored value is never read "
               "back, usually followed by `andi $rX, $aN, 0xffff` (or 0xff).",
    means="Parameter N is declared NARROW (u16/s16, or u8/s8 for 0xff). IDO "
          "homes a narrow parameter to its stack slot on entry and re-derives "
          "the clean value with an andi. A parameter typed s32 produces "
          "neither instruction.",
    prescription="Declare that parameter as the narrow type -- u16/s16 for an "
                 "0xffff mask, u8/s8 for 0xff. Widening it to s32 loses both "
                 "the store and the andi and cannot match. To narrow further, "
                 "reassign to the parameter itself (`arg0 &= 0xFFF;`) rather "
                 "than introducing a local, which would move it to another "
                 "register.",
    confirmed_on=[
        "SBK1 setRaceCameraMode(u16, u16): sw a0,0(sp) / andi t6,a0,0xffff and "
        "sw a1,4(sp) / andi t7,a1,0xffff",
        "Negative control: getRaceItemEffectType(s32) emits no home store",
    ],
))

register(Pattern(
    id="stale-a3-not-an-argument",
    name="Dead argument register at a callsite",
    kind="review",
    looks_like="A callsite where $a3 (or a higher arg register) still holds a "
               "value that the callee never reads.",
    means="IDO leaves an incoming argument sitting in the register and never "
          "clears it. It is stale, not a fourth parameter.",
    prescription="Do NOT add a parameter to the callee to explain it. Count "
                 "arity from what the callee actually consumes.",
    confirmed_on=["DECOMPILATION_LEARNINGS.md, IDO parameter homing section, "
                  "recorded from the reference decomp's own matching work"],
))


_SLL_RE = re.compile(r"\bsll\s+\$?(\w+),\s*\$?(\w+),\s*(?:0x)?([0-9a-fA-F]+)")
_HOME_RE = re.compile(r"\bsw\s+\$?(a[0-3]),\s*(-?(?:0x)?[0-9a-fA-F]+)\(\$?sp\)")
_ANDI_RE = re.compile(r"\bandi\s+\$?\w+,\s*\$?(a[0-3]),\s*0x(ffff|ff)\b", re.I)


def detect_narrow_params(asm: str) -> dict[str, int]:
    """Map argument register -> narrow width in bytes, from entry homing.

    `sw $aN, K($sp)` plus `andi $rX, $aN, 0xffff` means parameter N is 16-bit;
    0xff means 8-bit. An s32 parameter emits neither, so absence is meaningful
    too. Confirmed against setRaceCameraMode(u16,u16) and the s32 control
    getRaceItemEffectType.
    """
    head = "\n".join(asm.splitlines()[:14])
    homed = {m.group(1) for m in _HOME_RE.finditer(head)}
    if not homed:
        return {}

    widths: dict[str, int] = {}
    for m in _ANDI_RE.finditer(head):
        reg, mask = m.group(1), m.group(2).lower()
        if reg in homed:
            widths[reg] = 2 if mask == "ffff" else 1
    return widths


def narrow_param_hint(asm: str) -> str:
    widths = detect_narrow_params(asm)
    if not widths:
        return ""
    lines = []
    for reg in sorted(widths):
        idx = int(reg[1])
        w = widths[reg]
        lines.append(f"  parameter {idx} (${reg}) is {w*8}-bit: declare it "
                     f"{'u16/s16' if w == 2 else 'u8/s8'}, not s32")
    return ("\nNARROW PARAMETERS DETECTED (dead home store + andi at entry):\n"
            + "\n".join(lines) +
            "\n  Typing these wider drops both the store and the andi and "
            "cannot match.\n")
_ADDU_RE = re.compile(r"\baddu\s+\$?(\w+),\s*\$?(\w+),\s*\$?(\w+)")


def decode_array_stride(asm: str) -> list[int]:
    """Recover struct sizes from index arithmetic.

    IDO turns `arr[i]` into a shift/add chain that multiplies the index by the
    element size. `sll 2; addu self; sll 2` is (i*4 + i)*4 = i*20, so the
    element is twenty bytes. That is not a hint or a heuristic -- the stride is
    arithmetically encoded in the instructions and can be read straight out.

    A stride mismatch needs a layout hypothesis. The permuter can mutate types,
    but that does not guarantee recovery of a missing struct layout. SBK1
    unlockRelocatableHeapBlock declared 18 bytes instead of 20 and
    produced exactly that, `sll 3; addu; sll 1` (=18) against a target of
    `sll 2; addu; sll 2` (=20), and 300s of permuting moved it nowhere.

    Returns plausible strides, largest first. Conservative: only the shift/add
    forms IDO actually emits are decoded.
    """
    lines = asm.splitlines()
    strides: set[int] = set()

    for i, line in enumerate(lines):
        m = _SLL_RE.search(line)
        if not m:
            continue
        dest, src, sh = m.group(1), m.group(2), int(m.group(3), 16)
        factor = 1 << sh

        # Look ahead a few instructions for `addu dest, dest, src` (i*2^n + i)
        # then an optional second shift.
        for nxt in lines[i + 1:i + 5]:
            a = _ADDU_RE.search(nxt)
            if a and a.group(1) == dest and {a.group(2), a.group(3)} == {dest, src}:
                total = factor + 1
                for tail in lines[i + 1:i + 8]:
                    m2 = _SLL_RE.search(tail)
                    if m2 and m2.group(1) == dest and m2.group(2) == dest:
                        total *= 1 << int(m2.group(3), 16)
                        break
                if 2 <= total <= 4096:
                    strides.add(total)
                break

    return sorted(strides, reverse=True)


def stride_hint(asm: str) -> str:
    strides = decode_array_stride(asm)
    if not strides:
        return ""
    listed = ", ".join(f"{s} (0x{s:x})" for s in strides)
    return (f"\nARRAY STRIDE DECODED FROM THE TARGET: {listed} bytes.\n"
            f"  The index arithmetic multiplies by exactly this, so any struct "
            f"you index must be EXACTLY this size. Pad it to match. A struct of "
            f"the wrong size produces different shift/add constants and can "
            f"never match, no matter how the rest is written.\n")


_FRAME_RE = re.compile(r"\baddiu\s+\$?sp,\s*\$?sp,\s*-(0[xX][0-9a-fA-F]+|\d+)\b")


def target_frame_size(asm: str) -> int | None:
    """Bytes of stack frame the target allocates, from its prologue."""
    m = _FRAME_RE.search(asm)
    return int(m.group(1), 16 if m.group(1).lower().startswith("0x") else 10) if m else None


def frame_hint(asm: str) -> str:
    """State the observed frame size without inventing source-level causes.

    Decompedia follow-up probes with the installed SBK1 compiler disproved
    unconditional prototype-spelling and named-temporary stack-cost rules.
    Receipts: eval/experiments/decompedia/results-v1/receipt.json.
    The permuter ignores stack positions by default; its optional stack-aware
    scoring is documented as weak, not categorically incapable of improvement.
    """
    size = target_frame_size(asm)
    if size is None:
        return ("\nThe target allocates NO stack frame (no `addiu sp,sp,-N`). "
                "Keep the function frameless: avoid locals that must be spilled "
                "and avoid calls.\n")

    return (f"\nTARGET STACK FRAME: {size} bytes (`addiu sp,sp,-{size}`).\n"
            f"  Compare the candidate frame and stack accesses with the target. "
            f"A frame mismatch can shift several offsets at once.\n"
            f"  Inspect saved registers, address-taken locals, spills and outgoing "
            f"call arguments; test each proposed source change with the compiler.\n"
            f"  Prototype spelling and naming a temporary do not imply a fixed "
            f"stack-size change.\n"
            f"  The permuter ignores stack positions by default; --stack-diffs "
            f"enables stack scoring but is documented as weak.\n")


def hints_for_asm(asm: str) -> str:
    """Guidance that applies to THIS target, assembled from confirmed patterns.

    This is the accumulated-knowledge tool: every prescription that has been
    verified against a real function, applied automatically whenever its shape
    shows up. A pattern with no `confirmed_on` never fires -- a hypothesis does
    not get to steer the solver.
    """
    if not asm:
        return ""

    fired = []
    for p in CATALOG.values():
        if p.is_hypothesis or p.detector is None or p.kind == "evidence":
            continue
        try:
            if p.detector(asm):
                fired.append(p)
        except Exception:
            continue

    # Computed facts read straight off the target: array stride, narrow
    # parameter widths, stack frame size. These are arithmetic, not pattern
    # matching, so they are always included when present.
    #
    # These were absent for several iterations because the edit that was meant
    # to wire them in never matched, and the patch script printed "patched"
    # unconditionally. Three hypotheses were marked CONFIRMED on evidence that
    # could not have come from them. tests/test_units.py now asserts the
    # composition so it cannot silently detach again.
    computed = stride_hint(asm) + narrow_param_hint(asm) + frame_hint(asm)

    if not fired:
        return computed

    lines = ["\nIDIOMS DETECTED IN THIS TARGET (each confirmed on a real match):"]
    for p in fired:
        lines.append(f"- {p.name}: {p.means}")
        lines.append(f"  -> {p.prescription}")
    return "\n".join(lines) + "\n" + computed


# ------------------------------------------------- uopt trace-diagnosed shapes (2026-09-14)
# Found by reading uopt's own colouring trace against the target (solver/uopt_diagnosis.py);
# each became a generator in solver/regalloc_mutations.py with a fire test on these receipts.

register(Pattern(
    id="uopt-unforwarded-typed-reread",
    name="Stored value re-read through the other signedness stays a ugen temporary",
    kind="solver",
    looks_like="Target: `addiu t8,v0,-4 ... bnez t8 ; sw t8,off(a0)` -- a computed value in a ugen "
               "temporary (t6-t9) feeding both a field store (often in a delay slot) and a test. "
               "Candidate `t = E; FIELD = t; if (t ...)` holds E in a uopt-coloured register (v0/v1).",
    means="uopt forwards a store into a reload of the same type, which makes E a shared value with "
          "its own live range. A reload through the other signedness (s32 store, u32 read) is not "
          "forwarded, so E is used once in u-code and stays a ugen temporary; as1 then removes the "
          "reload by forwarding the store.",
    prescription="Drop the local: `FIELD = E;` and test the field re-read at flipped signedness "
                 "(regalloc_mutations.typed_field_rereads). Either store or read may carry the flip.",
    confirmed_on=["eval/results/uopt-trace-20260914/guided: updateRaceUiScorePopupSlideIn, "
                  "updateRaceSetupNamePlateSlideIn, updateRaceUiCrashScorePopupSlideIn, "
                  "updateRaceUiTrickScorePopupSlideIn, updateTimeTrialRecordDeltaPopupSlideIn (object-exact, "
                  "verify.jsonl)"],
))

register(Pattern(
    id="uopt-narrow-truth-test-priority",
    name="Testing a narrow local itself shortens its widened copy and swaps colours",
    kind="solver",
    looks_like="A u8/u16 local used in a switch and in a later `!= 0` test; target keeps the local in v1 "
               "and its widened copy in v0 (`move v0,v1`), candidate has them the other way round.",
    means="The widened copy (ucvt) is its own uopt live range. When the later test also uses it, the "
          "range spans to the end, its adjsave (save per block) falls below the local's, and the local "
          "is coloured first into v0. `if (x)` tests the narrow value, the copy's range shrinks to the "
          "switch head, and it outranks the local.",
    prescription="`if (x != 0)` -> `if (x)` on narrow locals (regalloc_mutations.narrow_truth_tests). "
                 "Changing the local to u32 instead removes the copy and breaks the code shape.",
    confirmed_on=["eval/results/uopt-trace-20260914/guided: updateCharacterSelectRosterIcons (object-exact; "
                  "trace adjsave 2.0 -> 2.5 for the copy, 3.0 -> 1.25 for the local)"],
))


# ---------------------------------------------------------------- reporting

register(Pattern(
    id="input-cursor-advance-at-load",
    name="Advance a single-use input cursor at its load instead of its return",
    kind="solver",
    looks_like="A draft loads through a pointer parameter, never uses that pointer again until returning p+1, "
               "while the target advances its argument register immediately after the load.",
    means="The source positions the pointer update too late. On the exposed Fdistort development state, "
          "widening the sign-extension temporary alone reached 81%; combining it with an advance at the load "
          "reached object-exact. This is a source-shape hypothesis confirmed by compilation, not a new type fact.",
    prescription="solver.cursor_advance.variants proposes v=*p++; ... return p, through the existing "
                 "regalloc_mutations registry. It requires a simple pointer parameter, exactly two uses, an "
                 "unconditional load and final return, and no intervening pointer use or loop/jump. Keep "
                 "compiler verification authoritative. The motivating dev state is excluded from training.",
    confirmed_on=["eval/results/residual-repair-20260922/paired-search.json: Fdistort, header-assisted; "
                  "same 40-compile ceiling, old search 24 compiles/nonexact, new search 10/object-exact; "
                  "fresh independent workspace and frontend verification"],
))

register(Pattern(
    id="mmio-symbol-literal-and-register-poll",
    name="Fixed hardware-address loads and a register poll temporary",
    kind="review",
    looks_like="The binary reloads a hardware status word through t6/t8 into a3, retains an 8-byte frame, "
               "and forms a KSEG1 access with lui at,0xa000; the draft uses ordinary symbol lvalues.",
    means="On osEPiRawWriteIo and osEPiRawReadIo, a register u32 polling local plus explicit volatile "
          "hardware-address accesses reproduces the ROM bytes. Volatile alone did not change codegen. "
          "The literal address values were read from target instruction words, not reference C.",
    prescription="This is a recorded experimental repair, not an automatically enabled MMIO generator. "
                 "Use binary-derived addresses and verify with the existing schema-3 function certificate; "
                 "symbolic-vs-literal relocation spelling can keep the object score below 100 despite "
                 "identical linked function bytes. No claim of whole-ROM integration.",
    confirmed_on=["eval/results/residual-repair-20260922/paired-search.json: both I/O functions freshly "
                  "ROM-backed function-exact, schema 3, frontend passing, binary-only candidates"],
))


def summary() -> str:
    lines = []
    by_kind: dict[str, list[Pattern]] = {}
    for p in CATALOG.values():
        by_kind.setdefault(p.kind, []).append(p)

    for kind in ("evidence", "solver", "review"):
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"\n{kind.upper()} ({len(items)})")
        for p in items:
            mark = "?" if p.is_hypothesis else "*"
            lines.append(f"  [{mark}] {p.id:26} {p.name}")
            if p.is_hypothesis:
                lines.append("        HYPOTHESIS -- not confirmed, must not change behaviour")
    return "\n".join(lines)


if __name__ == "__main__":
    print(f"{len(CATALOG)} patterns catalogued")
    print(summary())
    unconfirmed = [p.id for p in CATALOG.values() if p.is_hypothesis]
    if unconfirmed:
        print(f"\nunconfirmed: {', '.join(unconfirmed)}")


# ------------------------------------- IDO 5.3 allocator rules tested against 7.1 claims (2026-09-23)
# Protocol and receipts: eval/results/allocator-rules-20260923/ (PROTOCOL.md written before any test;
# eval/allocator_rules.py over the 115-TU uopt trace census, eval/allocator_interventions.py paired compiles).
# 7.1 source: akratch/ssb64-func_ovl0_800CEF4C-frontier docs/allocator-model.md (instrumented 7.1 uopt).

register(Pattern(
    id="uopt53-priority-block-units",
    name="IDO 5.3 live-range priority is an integer save over a step function of the block span",
    kind="evidence",
    looks_like="uopt's level-5 `adjsave` for a live range, e.g. 31/14 at a 50-block span, 155/6 at 19 blocks.",
    means="adjsave = save / units(span), units(raw) = raw if raw < 3 else ((raw - 2) >> 2) + 2, raw = the "
          "number of basic blocks the range is live in. 11,808 of 11,809 census ranges (shuffled spans: 69.7%). "
          "The step is 7.1's, but 7.1's raw adds the reference count; that pre-registered form fit 12.6% "
          "(shuffled 11.9%) and is refuted. POST-HOC: read off the pre-registered test's misses, then checked on "
          "all ranges.",
    prescription="Predict a range's colouring priority from its block span and save; lengthening a range "
                 "across blocks lowers its priority in steps, not per block.",
    confirmed_on=["uopt trace census 2026-09-14: 115 SBK1 TUs, 11,809 live ranges"],
))

register(Pattern(
    id="uopt53-save-counts-reads",
    name="IDO 5.3 save rises by 1 per read of the variable and not at all per write",
    kind="solver",
    looks_like="A variable's adjsave changes when a use is added or removed, with no change to its block span.",
    means="Paired compiles (traced 5.3 uopt, game -O2 recipe) on three bases: one more read +1 save in 3 of 3, "
          "one more write +0 in 3 of 3. 7.1 is described as +10 per reference of either kind. Save can reach "
          "zero or below (`not colored (-ve save)`): a two-call function's local had adjsave 0 and stayed in "
          "memory, so 5.3 also subtracts a cost the 7.1 description does not mention. Loop weighting untested "
          "(the loop split the range).",
    prescription="To raise a variable's colouring priority add a read, not a write; to lower it remove reads.",
    confirmed_on=["syn_f leaf, leaf_branch, across_call (eval/allocator_interventions.py)"],
))

register(Pattern(
    id="uopt53-empty-test-steers-priority",
    name="`if (!x);` raises x's priority by exactly one read, and in leaf code emits nothing",
    kind="solver",
    looks_like="A register-only residual where one variable must win (or lose) a colour against a neighbour.",
    means="The empty conditional is one read of x to uopt (+1 save in 3 of 3 bases, equal to an extra real "
          "read) and IDO emits no instruction for it: the object was identical in both leaf bases; across a "
          "call the changed priority changed the allocation. 7.1 describes the same trick at +10. A bare `x;` "
          "left object and priority unchanged in 3 of 3 but not the trace text (numbering), so the "
          "pre-registered 'inert' criterion failed on the trace; the object claim holds.",
    prescription="A zero-code priority knob for register residuals: add `if (!x);` to lift x one read above "
                 "an equal-priority neighbour.",
    confirmed_on=["syn_f leaf, leaf_branch, across_call (eval/allocator_interventions.py)"],
))

register(Pattern(
    id="uopt53-colouring-order",
    name="IDO 5.3 colours constrained ranges by descending priority, ties in live-range order; the rest in "
         "live-range order regardless of priority",
    kind="evidence",
    looks_like="The order of uopt's level-6 colouring decisions.",
    means="Constrained: descending adjsave in 98.9% of 8,640 consecutive pairs; equal-adjsave ties in "
          "increasing live-range number (first u-code store) in 99.6% of 3,471. Unconstrained: live-range order "
          "in 875 of 875 pairs, priority order in only 66%. 7.1's 'all webs by descending priority, ties by "
          "first source appearance' holds for constrained ranges only. Lowest-free selection (7.1) fits 89.5% "
          "of 11,310 decisions; the 5.3 preference model in solver/uopt_trace.py fits 99.9%.",
    prescription="For unconstrained ranges, which variable is first STORED decides colouring order; for "
                 "constrained ones priority does, then first store.",
    confirmed_on=["uopt trace census 2026-09-14: 1,976 procedures, 11,310 decisions"],
))

register(Pattern(
    id="uopt53-local-offsets",
    name="IDO 5.3 gives word locals frame offsets -4, -8, -12, ... in declaration order",
    kind="evidence",
    looks_like="uopt isvar kind M with a frame offset (`isvar M 3 -8vreg`).",
    means="Three declaration orders of the same three word locals, each mapped by the confirmed +1-read probe: "
          "the first declared local is always -4, then -8, -12 (eval/allocator_interventions.py h7). Only word "
          "locals were tested; a wider or array local ends the rule.",
    prescription="Map a uopt live range to its C variable by declaration order instead of by probing.",
    confirmed_on=["syn_f a/b/c in orders abc, cab, bca", "waitCourseSelectRecordsClose var_s1 -4, var_s0 -8"],
))

register(Pattern(
    id="ido53-frame-layout",
    name="IDO 5.3 frame = align8(outgoing) + align8(saves) + align8(deepest memory-resident local), locals on top",
    kind="evidence",
    looks_like="`addiu sp,sp,-N` and `addiu aK,sp,OFF` / `sw ...,OFF(sp)` for address-taken or spilled locals.",
    means="Every declared local takes a virtual offset below the frame top in declaration order, used or not and "
          "register-allocated or not: a scalar aligned to its own size (char 1, short 2, word 4, double 8), an "
          "array or struct to max(4, its alignment). The locals area is only as deep as the deepest local that "
          "lives in memory (address taken, or spilled to its home). Saves: ra at the top of the save area, "
          "s-registers descending. Outgoing: max(16, 4 x widest call's words), 0 in a leaf without calls. Fitted "
          "on 7 constructs (H15 refuted); H15b 21/22 on 7 fresh ones (short at -6, not -8); H15c 12/12 on 6 more "
          "(eval/results/frame-size-20260923).",
    prescription="A frame-size or sp-offset residual is a declaration residual: the target's slot for an addressed "
                 "local fixes how many bytes of declared locals precede it, and in which order and widths.",
    confirmed_on=["syn_f H15b P1-P7 (21/22)", "syn_f H15c Q1-Q6 (12/12)"],
))

register(Pattern(
    id="ido53-o1-register-local-saved",
    name="IDO 5.3 -O1: a `register` local takes a callee-saved register; at -O2 `register` is inert",
    kind="solver",
    looks_like="-O1 target `sw s0,N(sp)` save plus `move s0,v0` after a call, where the candidate stores the value "
               "to its stack slot (`sw v0,N(sp)`, later `lw`); m2c names the local after the register (`temp_s0`). "
               "Typical: libultra `saveMask = __osDisableInt(); ... __osRestoreInt(saveMask);`.",
    means="At -O1 a plain local lives in its stack slot; declared `register`, it is held in a callee-saved register "
          "(even when not live across a call) and the frame grows by that register's save. `register` on a "
          "parameter changes nothing (the incoming home store stays). At -O2 `register` has no effect: 36/36 -O2 "
          "reference functions compile byte-identically with it removed, while 14/14 -O1 ones change. "
          "(eval/results/register-o2-20260924: H6 P6a holds, P6b/P6c/P6d refuted as registered; H7 P7a 14/14, "
          "P7b 36/36.) The lead came from draft-to-reference mining over non-population functions "
          "(eval/results/draft-reference-mining-20260924, `opcode:move/sw` -> `register+`).",
    prescription="-O1 recipe and a target-only callee-saved save: mark the local `register`, preferring the one m2c "
                 "named after the wanted register, and drop a pipeline-invented framePad "
                 "(solver.branch_shape.o1_register_saved). __osSetGlobalIntMask 75.8 -> 100 and "
                 "__osResetGlobalIntMask 79.3 -> 100 (receipts 95899, 95897, with frontend_type_repair "
                 "prototypes); osStartThread 85.2 -> 89.1. Do not propose `register` at -O2.",
    confirmed_on=["syn_f H6 P6a", "H7 ablation: 14/14 -O1 change, 36/36 -O2 identical",
                  "__osSetGlobalIntMask", "__osResetGlobalIntMask"],
))

register(Pattern(
    id="ido53-select-keeps-arm-paths",
    name="IDO 5.3 -O2 keeps one path per if/else arm to the join; default-then-override has one",
    kind="solver",
    looks_like="Target `li A; b!c join; nop; b join; li B` (a `b` to the join with the then-value in its delay "
               "slot), candidate `li A; b!c join; nop; li B` from m2c's `x = A; if (c) x = B;`.",
    means="`if (c) x = B; else x = A;` and `x = c ? B : A;` compile identically, with one more path to the join than "
          "`x = A; if (c) x = B;`. When the join is ordinary code the extra path is a `b join`; when it is a "
          "frameless return, the return tail is duplicated per arm. Where the else value lands (hoisted above the "
          "branch or in its own block) is as1 delay-slot scheduling, not C. An empty `else {}` equals no else. "
          "m2c flattens the target's shape to the default form because the else value sits above the branch. "
          "(eval/results/branch-layout-20260924: E1; H1 refuted on b-next specifics, H1' P1'c 2 vs 1 paths.)",
    prescription="Target has more unconditional branches than the candidate at a select: rewrite `x = A; if (c) "
                 "x = B;` to if/else (solver.branch_shape.select_else). drawTrainingCourseLessonEndMenu, "
                 "updateEndingLindaHandshakeLoop 76.4 -> 96.1.",
    confirmed_on=["syn H1 P1b/P1c/P1f", "syn H1' P1'b/P1'c", "drawTrainingCourseLessonEndMenu fire test"],
))

register(Pattern(
    id="ido53-o1-local-frame-epilogue",
    name="IDO 5.3 -O1: a declared local gives a leaf a frame, and every return then branches to one epilogue",
    kind="solver",
    looks_like="-O1 target: leaf `addiu sp,sp,-8`, returns as `b epilogue`, one `jr ra`; candidate frameless with "
               "a `jr ra` per return. The value sits in an argument register not holding a parameter (a0, or a1).",
    means="At -O1 (libultra io/*) locals get a frame of align8(4 x locals) even with no store; with no local each "
          "return is its own `jr ra`. A `register` local has the frame and no stack store (H2, 4/4 on fresh "
          "constructs; eval/results/branch-layout-20260924). At -O2 none of these forms has a frame.",
    prescription="-O1 function whose target has the frame and `b` returns: read the tested global into a "
                 "`register` local first (solver.branch_shape.o1_register_local). __osAiDeviceBusy 65.8 -> 98.3, "
                 "__osSi/SpDeviceBusy 62.6 -> 98.2, __osSpSetPc 67.6 -> 97.7. Residual: the target computes the "
                 "address in t6 and loads into a0; the direct extern read uses a0 for both (open).",
    confirmed_on=["syn H2 P2a-P2d (4/4)", "__osAiDeviceBusy, __osSpSetPc fire tests"],
))

register(Pattern(
    id="ido53-return-tail-per-statement",
    name="IDO 5.3 -O2 emits one return tail per return statement, and duplicates a return in each unrolled copy",
    kind="solver",
    looks_like="Two `li v0,-1; jr ra` tails in the candidate where the target has one (m2c comments "
               "`Duplicate return node`).",
    means="Two `return -1;` statements give two tails, a `break` to one `return -1;` gives one (H3' 2/2); IDO does "
          "not merge identical tails. A loop IDO unrolls (4x for a small body with no call) copies an in-loop "
          "return into every unrolled body (H3 P3a: 5 copies).",
    prescription="Where m2c marks a `Duplicate return node`, route it to the one return it duplicates "
                 "(solver.branch_shape.dup_return_merge). __MusIntFindChannel 90.864 -> 100.0.",
    confirmed_on=["syn H3' P3'a/P3'b", "__MusIntFindChannel fire test"],
))

register(Pattern(
    id="uopt53-loop-exit-test-rewrite",
    name="uopt rewrites a loop's `<` exit test to `!=` (bne against a hoisted bound) except in some loops",
    kind="review",
    looks_like="Target `slti at,iv,N; bnez at,loop`, candidate `bne iv,sK,loop` with `li sK,N` before the loop.",
    means="For, while, do-while and goto loops with a constant bound all get `bne` (E3). `slt` is kept for a narrow "
          "(short/u8) loop variable (with sign/zero extension), an unknown start value, a bound reloaded each "
          "iteration, a variable-bound goto loop (H4 P4a), and on the earlier loops when one variable drives "
          "adjacent loops (P5a). H5, 'reused by any later loop', was refuted (P5c, P5d), so the selector is "
          "open. m2c splits one variable's webs into var_R, var_R_2 ...; merging the webs that share a register "
          "in the target restored the target's `slt` loops in drawTrainingCourseLessonEndMenu (fire test).",
    prescription="Target keeps `slt` where the candidate has `bne`: propose merging m2c's split webs of one "
                 "register (solver.branch_shape.split_merge); the compiler decides.",
    confirmed_on=["syn H4 P4a/P4b", "syn H5 P5a/P5b", "drawTrainingCourseLessonEndMenu fire test"],
))

register(Pattern(
    id="uopt53-empty-test-adds-blocks",
    name="An empty `if (!x);` adds two basic blocks to every range live across it, and after a constant "
         "assignment adds no read",
    kind="solver",
    looks_like="Block spans growing by 2 per inserted empty test, which can push units(span) up a step.",
    means="After `x = 0;` the test is folded (save unchanged) yet x's span grew 7 -> 9 (h6); in "
          "waitCourseSelectRecordsClose each empty test added 2 blocks to both live ranges, so priority "
          "falls in steps as the span crosses 3, 7, 11 ... Emitted no instruction there. In the two leaf "
          "bases the span did not change, so block growth depends on context. H16 "
          "(eval/results/empty-test-reads-20260923): straight-line after a load, computed or call-result "
          "assignment, +1 save and +2 span (3/3). Inside a conditional arm, right after the arm's assignment, "
          "+0 save and +1/+2 span for a constant, a copy AND the computed control (3/3): the read is lost there "
          "whatever the value, so the test only costs blocks. In the population the inverter's raise moved the "
          "target's register in 2 of 30 measured candidates, and on blocked ranges save stayed flat at every k. "
          "Unexplained: `temp_v0 = call(); if (!temp_v0);` in enqueueSoundEffectWithVolume (straight-line, not "
          "live across a call) also added +0. The arm finding is NOT general: in freeRelocatableHeapBlock an "
          "empty test inside an arm moved temp_v0 to v0 (eval/results/alloc-inverter-20260923/lost_check.py).",
    prescription="Simulate both effects when steering: the read raises save, the blocks can lower priority "
                 "of the range and of every range live across it. Do not count the read after a constant or a "
                 "copy of a local/parameter (a global is a load and counts); verify the colour in the trace "
                 "(action_trace.py) rather than trusting the formula.",
    confirmed_on=["syn_f h6 base", "waitCourseSelectRecordsClose k = 1, 2, 3, 10", "syn_f H16 3/3, H16b 3 arms"],
))

register(Pattern(
    id="uopt53-loop-read-weight",
    name="A read inside a loop adds 10 to save (1 outside)",
    kind="solver",
    looks_like="adjsave jumping by 10/units per added use in a loop body.",
    means="waitCourseSelectRecordsClose: 1, 2, 3 and 10 empty tests after `var_s0 += 1;` in the do-while "
          "raised save 31 -> 41, 51, 61, 131: exactly 10 per read. Nested loops untested.",
    prescription="One read in a loop is worth ten outside it; compute k accordingly.",
    confirmed_on=["waitCourseSelectRecordsClose (4 compiles)"],
))

register(Pattern(
    id="ugen53-temp-fifo",
    name="IDO 5.3 ugen allocates expression temporaries from a FIFO cycle t6 t7 t8 t9 t0 ... t5, minus uopt's "
         "registers; as1 then reorders the instructions",
    kind="evidence",
    looks_like="`t8` where the target has `t0`: the whole temporary sequence is shifted by the number of "
               "allocations that differ before that point.",
    means="7.1 reg_mgr (LLONSIT/ido-decomp, read) describes a FIFO free list t6..t9, t0..t5, head allocated, freed "
          "registers appended. On 5.3, over the matched targets of 1,632 functions with temporaries (uopt-coloured "
          "registers removed from the pool using each procedure's own trace): 95.1% of 27,371 allocated registers "
          "are the ones the cycle predicts, order-free; 1,013 functions match exactly, 617 match once 3,694 "
          "allocated-but-never-emitted registers are allowed, 2 disagree. Scored in emitted order it looked like 62% "
          "because as1 interleaves ugen's allocation order (synthetic probe: allocated t6 t7 t8 t9, emitted "
          "sll t6, sll t8, sra t7, sra t9). A value computed straight into an argument or return register takes no "
          "temporary. The unused slots are as1 copy propagation: `cc -S` emits ugen's own output before as1, and there the sequence has no gap (syn_h3 probe: mul $24 (t8); move $2,$24 -- as1 folded both into `sll v0,a1,2`, removing t8). So a skipped slot is a temporary whose only use is a move into another register.",
    prescription="Read a temporary-numbering residual as an allocation count: the cycle distance between the "
                 "target's and the candidate's register is how many temporaries the source must add (or remove) "
                 "before that point.",
    confirmed_on=["uopt trace census TUs: 1,632 matched functions", "syn_f stores/args/return probes"],
))

register(Pattern(
    id="uopt53-strength-reduced-index",
    name="A pointer stepping by the element size through an array is uopt's strength reduction of `&BASE[i]`",
    kind="solver",
    looks_like="Decompiled `p = BASE; do { ...p...; i += 1; p = (T *)((u8 *)p + K); } while (i < n);` whose "
               "residual is temporaries, scheduling and allocation that no local repair closes.",
    means="uopt rewrites indexed addressing in a loop into a stepped pointer; the decompiler copies the "
          "pointer. Compiling the original index form reproduces uopt's own reduction, so the temporaries and "
          "the schedule follow. waitCourseSelectRecordsClose sat at 97.39 through allocator steering (99.13) and "
          "a temporary-count repair (99.348); the index form with the loop's later uses read through the global "
          "just stored was exact on its own; the index form with member access through the array was 91.28.",
    prescription="solver/strength_inverse.py (family `index_form` in regalloc_mutations): propose both "
                 "variants; the compiler decides.",
    confirmed_on=["waitCourseSelectRecordsClose (receipt 95882; attribution.py)"],
))

register(Pattern(
    id="uopt53-member-offset-equivalence",
    name="IDO 5.3 compiles `p->m` and `*(T *)((u8 *)p + OFF)` identically",
    kind="solver",
    looks_like="A `field:offset` residual whose wrong offset is a struct member, often of a header type.",
    means="30 of 30 paired compiles identical (s32/s16/u16/s8/u8 x load/store x pointer parameter, pointer "
          "local, global struct array element; eval/results/offset-access-20260923/h10.json).",
    prescription="Apply a stated offset without touching the declaration: rewrite the attributed member access "
                 "to the explicit byte-offset form with the target offset (solver/evidence_site.py).",
    confirmed_on=["syn_f 30 pairs (h10.py)"],
))

register(Pattern(
    id="ido53-narrow-local-mask",
    name="A computed value assigned to a u8/u16 local gets `andi`; to s8/s16 a `sll`/`sra` pair; a narrow local "
         "loaded from a wider field narrows the LOAD instead",
    kind="solver",
    looks_like="A surplus `andi 0xff/0xffff` (or `sll`/`sra` 24/16) with no mask or cast on its source line.",
    means="Paired compiles (eval/results/mask-type-20260923/h13.json), s32 vs u8/u16/s8/s16 local: from a call, "
          "exactly one extra andi (unsigned) or extension pair (signed); from a sum, the mask plus one more "
          "instruction; from a wider field load, NO mask -- IDO emits the narrower load (lbu/lhu) instead; as a "
          "loop counter the whole loop compiles differently (s32: 28 instructions, narrow: 14). Pre-registered "
          "criterion (exactly one extra instruction in every shape) held only for the call shape: PARTIAL.",
    prescription="solver/evidence_site.py: a surplus mask/extension on a line assigning a u8/u16/s8/s16 local "
                 "retypes that local to s32. Replay: 11 of 16 candidates improved, 9 functions.",
    confirmed_on=["syn_f call shape (4 types)", "drawMenuAsciiTextDefaultScale +12.94 (replay)"],
))

register(Pattern(
    id="uopt53-arg-preference",
    name="Passing a value as call argument k gives its live range a preference for a(k); returning it gives no v0 "
         "preference",
    kind="evidence",
    looks_like="A value held in an argument register (a0-a3) where the target holds it in a temporary, or the "
               "reverse: the `selection` diagnosis class.",
    means="Paired traced compiles (eval/results/alloc-preference-20260923): a local passed as argument 0, 1, 2, 3 "
          "gets preference colour 3, 4, 5, 6 and lands in a0..a3, 4 of 4; used only in arithmetic, no preference "
          "(lowest free, v0); returned, NO v0 preference (it took v1) -- refuted for returns; assigned a call's "
          "result, no allocator range at all (untestable).",
    prescription="To move a value into a(k), make it flow into argument k; to move it out, the target's C does not "
                 "pass that same value there. The allocator inverter's preference operator.",
    confirmed_on=["syn_f arg0..arg3, none (probe.py)"],
))

# ------------------------------------- object rows the normalized diff cannot show (2026-09-30)

register(Pattern(
    id="address-taken-rodata-names-the-target-label",
    name="A string whose address the function passes on must be read through the target's rodata label",
    kind="solver",
    looks_like="Byte-identical .text; the candidate owns the literal in its own .rodata (or names it with an extern "
               "the ROM does not define) while the target addresses a labelled datum. Score 99.9-100, empty or "
               "spelling-only diff; function_boundary refuses 'address-taken candidate rodata needs its named symbol', "
               "'candidate rodata the function does not read' or 'unresolved candidate external'.",
    means="The split-asm target keeps every datum under a label (D_800E12F4); a function-owned literal is placed by "
          "the translation unit, not by the function, and drawRaceSplitscreenSelectEntryFee's own literal failed "
          "the whole-ROM checksum (2026-09-14). The C is right; the datum's name is not.",
    prescription="solver.rodata_symbol.address_variants: pair each candidate addiu site with the target relocation "
                 "at the same instruction offsets, read the exact target label, and rewrite the definition, the "
                 "extern or the anonymous literal (only when the source literals rebuild .rodata byte for byte) to "
                 "an extern of that label. The function certificate and whole-ROM integration decide.",
    confirmed_on=[
        "2026-09-30 func_8005A884, func_8005CF60, updateEndingObjectSpriteDebugViewer, func_8005C14C, "
        "func_8005D558, func_8005AC44: function_boundary refused each; each rewrite is function_exact (schema 3). "
        "Whole-ROM integration (corrected later 2026-09-30): the extern-label form of all six prepares but FAILS TO LINK "
        "(`undefined reference to D_800E...`: no linker script defines the label and only a TU literal ever emitted "
        "the bytes). The function-exact certificate is therefore not an integration claim for this form. The "
        "original inline-literal sources of six of the seven cases are whole-ROM exact, alone and as one batch of six "
        "(eval/results/hidden-object-20260930/linking_probe.py). "
        "eval/results/hidden-object-20260930/address_cases.json",
    ],
))

register(Pattern(
    id="struct-field-access-where-target-names-a-global",
    name="A struct field the target keeps as its own named global",
    kind="solver",
    looks_like="One relocation pair `%lo(A)` (target) against `%lo(B+K)` (candidate) at a load/store, where the "
               "candidate reaches B+K through a struct field `B.f`; score 99.8, a one- or two-line diff.",
    means="The target's data is split into separate symbols (`gRaceSetupPlayerCountPromptAlpha`) where the "
          "candidate's struct typing merged them. The access width in the target instruction fixes the type.",
    prescription="solver.relocation_names.field_names: for each field the function accesses on B, propose `B.f` -> A "
                 "(declared with the target instruction's width) for every use and for each single use. No layout "
                 "is read; the object oracle picks the right field and site. Wired into operand_repair._proposals "
                 "(relocation_names had no caller before 2026-09-30).",
    confirmed_on=[
        "2026-09-30 updateRaceSetupPlayerCountPrompt: 99.829 -> 99.915, function_exact (schema 3); the winning variant "
        "replaces only the second `.alpha` use (all-uses scored 93.4). Single instance: treat the rule as measured on "
        "one function. 33 of 222 unsolved functions with a named relocation mismatch are reached by any "
        "relocation_names generator.",
    ],
))

register(Pattern(
    id="unused-file-scope-object-adds-bss",
    name="An unused file-scope object gives the candidate a .bss the target lacks",
    kind="solver",
    looks_like="Empty or unchanged diff, every diff-driven lane declining ('no mismatching instruction mapped'), and "
               "an object whose only extra is .bss/.data; names like dummy, padding, spNN.",
    means="Stack-padding edits written at file scope, where they cannot change the frame and only add data.",
    prescription="solver.file_scope_objects.variants: when the target object has no .bss/.data, delete file-scope "
                 "object definitions no other line mentions. The compiler decides.",
    confirmed_on=[
        "2026-09-30 guMtxIdent: `float sp18[4][4];` removed -> object_sections_exact "
        "(eval/results/hidden-object-20260930/RESULTS.md)",
    ],
))

register(Pattern(
    id="unresolved-extern-names-the-target-symbol",
    name="An extern the ROM does not define, where the identical target names another symbol",
    kind="solver",
    looks_like="Byte-identical .text; a reloc_identity row with different external names at the same instruction "
               "offsets; function_boundary refuses 'unresolved candidate external'.",
    means="A reference-style or invented name for the object the target actually addresses. The instructions agree, "
          "so the target's name is the evidence.",
    prescription="solver.rodata_symbol.address_facts emits a symbol site only when .text is byte-identical; "
                 "address_rewrite renames every use and keeps the candidate's declared type. The certificate decides.",
    confirmed_on=[
        "2026-09-30 drawCharacterSelectCourseExitPreviewPanel: gCharacterSelectCourseExitPreviewCornerTile -> "
        "gCharacterSelectCourseExitPreviewData, object_sections_exact (eval/results/hidden-object-20260930/"
        "layer_run2.jsonl); whole-ROM integration blocked by the shared-declaration limit",
    ],
))


# ------------------------------------------------------------------ 2026-10-02: rules behind the planted-edit generators
# Each rule was first seen on planted single-line edits (eval/results/edit-capability-20261002) and then PROBED on
# fresh minimal constructs with the game's own recipes, -O2 game code and -O1 libultra io (eval/rule_probes.py,
# receipt eval/results/rule-probes-20261002/probes.json). The probe conditions, not the planted cases, define the
# rule; "invisible" conditions are recorded too, because they say where a search should not spend compiles.
_PROBES = "eval/results/rule-probes-20261002/probes.json"

register(Pattern(
    id="ido53-o1-result-temporary-has-a-stack-home",
    name="At -O1 a temporary holding a result is stored and reloaded; at -O2 it vanishes",
    kind="solver",
    looks_like="-O1 target returns straight from v0 in a frame 8 bytes smaller; the candidate adds `sw v0,N(sp)` / "
               "`lw v0,N(sp)` (or the same pair around another value) before the return.",
    means="`t = E; return t;` (and `t = E; S(t);`): at -O1 every local has a stack home, so the temporary costs a "
          "store, a reload and frame space. At -O2 the two spellings compile identically.",
    prescription="Inline the temporary into its single next use: solver.next_use_temp (site_edits gaps=True). At "
                 "-O2 do not spend compiles on this rewrite for a result temporary: it cannot change the object.",
    confirmed_on=[
        f"probes {_PROBES} return_temp: -O1 differ 4/4 (call value, arithmetic value, field load, call then more "
        "code); -O2 same 4/4.",
        "planted (edit-capability-20261002): `return E` -> temporary was invisible 118/124 on game code; the visible "
        "cases were -O1 libultra (osSpTaskYield, __osSiGetAccess, __osPiRelAccess, osVirtualToPhysical), all fixed "
        "by next_use_temp.",
    ],
))

register(Pattern(
    id="ido53-commutative-operand-materialisation-order",
    name="A commutative operator's operand order shows only where both operands are materialised separately",
    kind="solver",
    looks_like="Two loads feeding one add/mul appear in swapped order (`lw t7,12(a0); lw t6,8(a0)` vs `lw t7,8(a0); "
               "lw t6,12(a0)`) with the arithmetic instruction itself unchanged; or, for two register-held "
               "parameters, the arithmetic operands themselves swapped (`addu v0,a1,a2` vs `addu v0,a2,a1`).",
    means="IDO emits the operand LOADS in source order, and keeps source order for two register-resident operands. "
          "A load against a parameter, or anything against an immediate, is canonicalised: swapping the C operands "
          "changes nothing. Consistent with named-operand-local-order (operand order follows webs there).",
    prescription="Offer operand swaps only where both operands are loads or both are register-held values "
                 "(site_edits gaps=True restricts regalloc_mutations.commutative_swaps to attributed lines). Skip "
                 "`x OP const` and load-vs-parameter: invisible.",
    confirmed_on=[
        f"probes {_PROBES} commute_operands, -O1 and -O2 alike: differ for two s16 loads, two s32 loads, two sums "
        "in one call, two params, field*field; same for field+param and field&constant.",
        "planted: operand swaps invisible 36/37 (game code); the visible one swapped two field loads "
        "(drawEndingCreditsCharacterLoopingSparkle), fixed by the commutative lane.",
    ],
))

register(Pattern(
    id="ido53-o1-mirrored-comparison-keeps-source-order",
    name="`a < b` and `b > a` compile identically at -O2 but not at -O1 when both sides are values",
    kind="solver",
    looks_like="-O1: the two stack-home reloads feeding `slt` come in the other order and `slt at,t7,t6` vs "
               "`slt at,t6,t7`.",
    means="At -O1 each operand is reloaded from its home in source order. At -O2, and at -O1 against a constant, the "
          "comparison is canonicalised.",
    prescription="Mirror a comparison (swap operands, reverse the operator) only in -O1 functions where both sides "
                 "are variables or fields. Never at -O2.",
    confirmed_on=[
        f"probes {_PROBES} mirror_comparison: -O1 differ field-vs-field and param-vs-param, same field-vs-constant; "
        "-O2 same 3/3.",
        "planted: mirrored comparisons invisible 46/46 (game code -O2, mostly against constants).",
    ],
))

register(Pattern(
    id="ido53-adjacent-store-order-is-preserved",
    name="Two adjacent stores to different fields keep source order",
    kind="solver",
    looks_like="The same set of stores (`sh t6,0x18(a0)`, `sh t7,0x1a(a0)`) in a different order; the values' "
               "`li` instructions usually reorder with them.",
    means="IDO does not reorder independent stores, before a call or not. For constant stores the order is directly "
          "visible, unlike operand order. For two read-modify-write statements (`a->x -= 0x30; a->y += 3;`) IDO "
          "schedules loads and stores the same way in either order, and the swap is visible only as which temp "
          "registers the two webs get (planted stmt_swap updateRaceItemProjectileTrailEffect: identical "
          "instruction order, t7/t9/t0/t8 exchanged).",
    prescription="Swap or move the statements so the stores follow the target's order: the pool's `move statement` "
                 "and the mined `stmt_order` rules already do this (planted stmt_swap 6/6 dev, 6/6 held-out).",
    confirmed_on=[
        f"probes {_PROBES} store_order: differ 4/4 at both -O1 and -O2 (two constants, zero stores, read-modify-"
        "write, stores before a call).",
        "planted: adjacent store swaps visible 12/12 (dev and held-out), none normalised.",
    ],
))

register(Pattern(
    id="ido53-empty-arm-layout-conditions",
    name="An empty then-arm (`if (!(c)) {} else {B}`) is usually folded, but not always",
    kind="solver",
    looks_like="Branch sense and block order differ around one if: `bnez` vs `beqz` plus an extra `b`, the arm's "
               "code moved.",
    means="MEASURED CONDITIONS, MECHANISM NOT EXPLAINED. Folded (identical to `if (c) {B}`) in 7 simple contexts at "
          "both levels. Visible at -O1 when the arm returns a computed value and code follows the if; visible at -O2 "
          "when the arm contains a nested if and code follows the outer if. A nested if alone, code after alone, or "
          "`return 1` were folded.",
    prescription="Offer rewrite_library.empty_arm_drops wherever an empty arm exists (cheap, one compile each). The "
                 "conditions above are not yet a detector: the residual cannot be attributed to this rule from the "
                 "diff alone.",
    confirmed_on=[
        f"probes {_PROBES} empty_then_arm (13 contexts x 2 levels): differ only for '&& test, body returns' (-O1), "
        "'simple test, body returns, code after' (-O1), 'return inside nested if, code after' (-O2), 'nested if "
        "without return, code after' (-O2); empty_else_arm same.",
        "planted: inverted ifs with an empty then-arm invisible 53/55; visible updateRacePlayerRecoverySparkle (-O2, "
        "nested if + code after) and osVirtualToPhysical (-O1, && test, body returns), both fixed by empty_arm_drops.",
    ],
))

register(Pattern(
    id="target-only-store-is-a-missing-statement",
    name="A store the target performs and the candidate never does is a missing statement",
    kind="solver",
    looks_like="The target has `sh/sw/sb R,OFF(aN)` (through aN or a saved copy `move sN,aN`) whose (opcode, offset) "
               "the candidate performs fewer times; its value is `zero`, a preceding `li R,IMM`, or `lh R0,OFF` + "
               "`addiu R,R0,IMM`.",
    means="A statement `argN->field = IMM;` / `argN->field += IMM;` is absent from the C. Nothing on the candidate side "
          "maps to it, so line-local edit families and the localizer cannot see it.",
    prescription="solver.missing_store (site_edits gaps=True) writes it from the target's own instructions (member "
                 "`unkOFF` or a raw offset store) at boundaries beside the charged lines. Values the target computes "
                 "(calls, arithmetic) go to solver.missing_statement_llm.",
    confirmed_on=[
        "planted (edit-capability-20261002): missing_store solved 4/6 dropped statements on dev and 3/6 on the frozen "
        "held-out set; the model lane solved 2 more on each.",
    ],
))

register(Pattern(
    id="load-opcode-names-the-access-type",
    name="A different load opcode at the same offset names the C type of the access",
    kind="solver",
    looks_like="Aligned target/candidate loads at one base+offset with different opcodes: `lw` vs `lh`, `lb` vs `lbu`, "
               "`lh` vs `lhu`.",
    means="The value is read through a different C type: lb s8, lbu u8, lh s16, lhu u16, lw s32. The type token is on "
          "the attributed line (a cast or the field's type) or in the declaration of an identifier used there. Until "
          "today this lived only in solver.evidence_site's docstring; this entry makes it citable.",
    prescription="solver.evidence_site opcode:<pair> rewrites the type token on the attributed line, else the "
                 "declaration of an identifier used on it. A per-location lh/lhu mix that compiles is a cast, not a "
                 "conflict (signedness-cast).",
    confirmed_on=[
        "eval/results/retrodiction-20260922: evidence_site rewrote global_load_signedness source-for-source on 5/5 "
        "of its successes.",
        "planted (edit-capability-20261002): decl_width updateControllerPakReplaySaveMessageSecondPageFadeIn, "
        "updateRaceSplitscreenSelectPortrait, initRaceSetupOpponentFocus fixed by evidence_site opcode:lh/lw and "
        "opcode:lb/lbu.",
    ],
))

register(Pattern(
    id="candidate-only-extension-widens-a-declaration",
    name="A sign/zero extension only the candidate performs names a declaration that is too narrow",
    kind="solver",
    looks_like="Candidate-only `sll R,X,0x10` + `sra R,R,0x10` (or 0x18, or `andi 0xffff/0xff`) on a value the target "
               "uses directly.",
    means="The residual form of s16-sign-extend: the candidate narrows a value to s16/s8 (or u16/u8) where the target "
          "keeps it wider, so IDO re-extends it. The narrowing is either a CAST on the use (`(s16) x`) or the "
          "DECLARATION of a variable the value passes through; the attributed line says which.",
    prescription="Rank decl/type edits that widen the named narrow type first (site_edits.widening_hint, gaps=True); "
                 "a cast on the attributed line is a type edit there, not a declaration change.",
    confirmed_on=[
        "planted (edit-capability-20261002): decl_width alLoadNew had the fix proposed only at rank 33 of 50 without "
        "the hint and first with it; decl_width 6/6 dev and 6/6 held-out with the gap lane.",
    ],
))


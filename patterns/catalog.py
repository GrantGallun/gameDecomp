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

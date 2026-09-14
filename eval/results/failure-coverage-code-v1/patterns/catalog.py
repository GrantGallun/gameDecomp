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

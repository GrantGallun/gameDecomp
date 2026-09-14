"""IDO codegen knowledge imported from the reference project's own notes.

Source: `DECOMPILATION_LEARNINGS.md` in the SBK1 decomp repo -- 1102 lines of
methodology accumulated by the humans who matched that game. It is METHODOLOGY,
not any function's source, so reading it carries no contamination: it says how
IDO behaves, never what a particular target's C looks like.

Everything here is registered as a HYPOTHESIS (`confirmed_on` empty) and
therefore cannot change behaviour. That is not a formality. The very first
entry we tested from this document -- that a scratch typedef prelude models a
different translation unit and costs points -- was REFUTED in our harness: only
2 of 37 candidates carried a prelude and swapping it for the real header moved
the score by exactly zero. The same document was simultaneously RIGHT about
alignment nops, which explained a failure sixteen automated variants could not.

So: a strong external methodology document is a source of hypotheses, not of
conclusions. Each entry below gets promoted only by a measurement in OUR
harness, recorded in patterns/hypotheses.py.

Why these entries and not the other thousand lines: our residual census says
register allocation is one of the largest remaining fault classes (36 faults on
renderRaceUiSingleTrailEffect, 38 on createCallbackTaskPreservingArgs, 20 on
drawMenuSolidRect), and we have no tool that addresses it at all. The subset
imported here is the part that bears on that, plus the measurements that
constrain which nudges could possibly work.
"""

from __future__ import annotations

from patterns.catalog import Pattern, register

SOURCE = "SBK1 decomp DECOMPILATION_LEARNINGS.md (methodology, not source)"

# ------------------------------------------------- measured allocator facts
# Measured by the reference team with an instrumented uopt from
# ido-static-recomp, gated on reproducing the stock object byte-identically
# with its trace variables unset. These constrain which nudges can work, so
# they are worth more than the nudges themselves.

register(Pattern(
    id="ido-t-registers-are-colored-not-temps",
    name="t0-t5 are in uopt's coloring pool, not proof of a ugen temporary",
    kind="evidence",
    looks_like="A t-register in the object at -O2 -mips1.",
    means="uopt colors lowest-index-first over c1=v0 c2=v1 c3=a0 c4=a1 c5=a2 "
          "c6=a3 c7=t0 ... c12=t5, then c14-c22 = s0-s8. A t-register is "
          "therefore an ordinary coloring outcome.",
    prescription="Do NOT classify t0-t9 as always-ugen. That misreads a plain "
                 "coloring difference as a temp-versus-pool 'class crossing' "
                 "and sends the search after web FORMATION when the real "
                 "question is web ORDERING. The always-ugen claim was probed "
                 "under -mips2 and does not hold here.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-web-ties-break-on-construction-order",
    name="Equal-priority webs take registers in construction order",
    kind="solver",
    looks_like="Two candidates identical except for which of two values holds "
               "the lower-numbered register.",
    means="Webs are colored in descending `save`, and ties break on web "
          "number, which is construction chronology. Two webs with identical "
          "interference neighbours are indistinguishable to the allocator, so "
          "whichever was built first takes the lower register.",
    prescription="Statement order at the CREATION site is a real dial: "
                 "swapping two adjacent pointer initialisations swaps their "
                 "registers. Try that before anything structural.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-web-priority-is-non-monotone",
    name="Adding reads can HALVE a web's allocation priority",
    kind="review",
    looks_like="A nudge that adds occurrences of a variable makes its register "
               "worse rather than better.",
    means="save = totalsave / nocs, with nocs = ((n - 2) >> 2) + 2 for n "
          "occurrences. nocs jumps at n = 2 and n = 6, so the quotient is "
          "coarse and non-monotone. One occurrence in a loop body can outrank "
          "three spread over a loop plus a cold block.",
    prescription="Compute the target before adding reads. Never assume 'more "
                 "uses raises priority'.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-dead-filler-is-not-allocation-neutral",
    name="A discarded read is a pure priority penalty on the web it reads",
    kind="review",
    looks_like="Dead filler such as `if (v) {}` added to reach the target "
               "instruction count, after which an unrelated register swaps.",
    means="The read adds occurrences, so it can push nocs over a step, but "
          "contributes nothing to totalsave -- the quotient can only fall. "
          "That can drop v's web below a competitor it would otherwise have "
          "tied with and beaten on web number.",
    prescription="If filler is load bearing, spell it against a variable that "
                 "is NOT part of the register residual. Reading the contested "
                 "variable buys instruction count at the price of its color.",
    confirmed_on=[],
))

# --------------------------------------------------------------- the levers

register(Pattern(
    id="ido-statement-order-is-an-allocation-lever",
    name="Order of independent sibling statements colors the downstream tail",
    kind="solver",
    looks_like="Same opcodes, same instruction count, same frame, permuted "
               "registers.",
    means="Statement order across INDEPENDENT sibling assignments is a "
          "first-class allocation lever. Twenty-four permutations of four "
          "mutually independent assignments moved positional word mismatches "
          "from 271 to 397 out of 422 with sequence, count, frame and stack "
          "homes all held constant. The same set was immune to all 65 "
          "declaration-order permutations and 183 blank-line layouts.",
    prescription="On a 'same opcodes, permuted registers' residual, permute "
                 "INDEPENDENT STATEMENT order before spending variants on "
                 "declarations, types or layout. Rank on POSITIONAL WORDS: "
                 "dist.py's reordering penalty hides this lever entirely, so "
                 "our score would not see it.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-value-kind-colors-the-argument-group",
    name="v0/v1/a0/a1 grouped by value kind, not source order",
    kind="review",
    looks_like="A symmetric pair of computations where the target interleaves "
               "the pair per computation rather than grouping them.",
    means="For a symmetric pair (two field loads feeding two shifts), IDO "
          "colors both loads with the low pair and both derived values with "
          "the high pair. Measured NON-levers for that grouping: local "
          "declaration order, statement order, per-axis nested scopes, "
          "expression grouping, separate carrier variables, source layout.",
    prescription="If the target interleaves instead of grouping, the original "
                 "source did not have the symmetric four-local shape. Look for "
                 "a different variable structure rather than permuting the one "
                 "you have -- the listed levers are already known dead.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-do-not-cache-what-the-target-reloads",
    name="Hoisting a repeatedly-read value into a local moves its register",
    kind="solver",
    looks_like="Instruction sequence matches; a base pointer or field value "
               "sits in the wrong register across calls.",
    means="IDO colors by internal temp numbering, not source variable "
          "identity. A named temp prevents a CSE the target performs, and "
          "hoisting prevents a reload the target performs.",
    prescription="If the target reloads a field before each use, reference it "
                 "directly each time instead of hoisting. If the target CSEs "
                 "two reads, avoid a named temp that would block it.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-compound-assignment-steers-result-register",
    name="Capturing a compound assignment keeps the sum where the target wants",
    kind="solver",
    looks_like="A load feeding a call argument lands in the wrong register.",
    means="`temp = (field += n);` keeps the unmasked in-register sum in place, "
          "where the two-statement form `temp = field + n; field = temp;` "
          "often puts it straight into an argument register.",
    prescription="Assign from the `+=` expression itself rather than through a "
                 "separate temp. Inlining it inside the consuming expression "
                 "can flip register homes where a separate statement does not.",
    confirmed_on=[],
))

register(Pattern(
    id="ido-redundant-mask-advances-the-temp-fifo",
    name="A redundant narrow-read mask phase-shifts allocation without surviving",
    kind="solver",
    looks_like="Registers are one step out of phase from the target while the "
               "instruction stream already matches.",
    means="`(u16_value & 0xFFFF)` is semantically a no-op after lhu, but "
          "instrumented IDO 5.3 shows it allocating and freeing a temporary "
          "even when the assembler deletes the instruction. Each stacked mask "
          "advances the FIFO one step.",
    prescription="Use as a last-mile dial when only register names differ. The "
                 "mask COUNT is the dial and only one exact count may land, so "
                 "enumerate rather than reasoning about it.",
    confirmed_on=[],
))

# ---------------------------------------------------------------- tooling

register(Pattern(
    id="ido-read-pre-as1-output",
    name="Diff `cc -S` output, not objects, for allocation residuals",
    kind="review",
    looks_like="An object diff showing dozens of shifted rows that reads as a "
               "'schedule' difference.",
    means="`cc -S` (same flags, minus -c) emits what ugen produced: final "
          "register numbers, .loc records and unexpanded pseudo-ops such as "
          "`div $15, $14, 2`. Register allocation is decided at that layer; "
          "as1 then reorders around the physical registers it is handed, and "
          "invents instructions (a homed-parameter reload becomes a `move`, "
          "`div x, y, 2` becomes a bgez/addiu/sra block).",
    prescription="Diffing two candidates' -S output isolates ONE changed "
                 "decision where diffing objects shows dozens of shifted rows. "
                 "An apparent basic-block boundary in the object need not "
                 "exist in the source. Most promising item imported here.",
    confirmed_on=[],
))

# The TOOL is verified present; whether diffing at that layer helps is not.
# Probed directly: tools/ido-recomp/linux/cc -O2 -mips1 -S on a trivial
# function emits final register numbers, .loc records, unexpanded pseudo-ops
# and .livereg masks --
#
#     .loc  2 1
#     mul   $2, $4, $5          <- as1 would expand this
#     addu  $2, $2, 3
#     .livereg 0x2000FF0E,0x00000FFF
#     j     $31
#
# ugen, uopt and umerge are all present in that directory too, so the
# instrumented-uopt route the reference team used is open to us as well.
# None of that is evidence the technique HELPS here, which is the part that
# still has to be measured before any of these entries is promoted.
IDO_S_FLAG_AVAILABLE = True

"""Does the campaign's intake open the front door on functions that DO NOT COMPILE?

HARNESS RULES, because every earlier "negative result" in this line of work turned out to be mine:

  * The sample is selected on THE THING UNDER TEST: `baseline_compiled == False` is measured, not
    assumed from "has no exact attempt". The first version selected the smallest functions with no exact
    attempt and got three that already compiled.
  * Every mechanism gets a REASON when it declines, so "had nothing to do" is distinguishable from
    "crashed" and from "was never given its inputs".
  * Any harness error (exception, missing context, unreadable target) is counted and printed as
    `errors`, and a run with errors is NOT reported as a rate.
  * Each mechanism is judged by compiling its OUTPUT. Firing is not success.
  * A sequential arm applies them in the campaign's order, recompiling and refreshing diagnostics after
    each -- because a mechanism that needs compiler diagnostics cannot be judged from the pre-repair
    diagnostics, and because that is what the campaign actually does.

NO MODEL IS CALLED and nothing is promoted. This measures whether exposing these mechanisms to the
policy can move a draft that does not compile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The campaign's order, TAKEN FROM THE CAMPAIGN. `eval/completion_campaign.py::_intake` applies the
# placeholder rewrite to every draft FIRST (it returns an additional candidate, never a replacement),
# then offers the header/symbol adapters, and lowers a do-while only when the compiler actually reported
# one. The first version of this probe had four entries in the wrong order with the placeholder rewrite
# missing entirely, so it judged `header_variant` on drafts whose front door was still shut: 0 of 40 on
# the size-bucketed frame, where the campaign's own first step opens 9.
# THE FRONTEND IS NOT IN THE SEQUENCE, and it took a miscalibrated ranking to see why. It is an
# OBSERVATION: it returns `changed: False`, so as a sequence step it can never convert anything and only
# adds a slot that makes the order harder to read. What it is FOR is reading the residual, and the residual
# has to be read where the candidate is. `--trace-frontend` now calls it after every step (`_diagnostic_chain`)
# instead of once at a fixed point, which is the difference between ranking the HEAD of a defect chain and
# ranking its DISTANCE. Measured: `renderRacePickupRespawn` reports 11 undeclared identifiers at position 2
# and zero after `header_variant` at position 4, so the single fixed read was reporting a candidate that no
# longer exists by the end.
SEQUENCE = ("eval.intake_runners.resolve_placeholders",
            # EARLY, because a dialect spelling is a SYNTAX blocker and cfe stops at the first one, so
            # everything behind it is invisible while it stands -- the same reason
            # `resolve_placeholders` runs first. It needs the local declarations m2c emits to resolve the
            # operand's type, which is why it follows the placeholder pass rather than leading.
            #
            # WIRED ON THE SECOND ASKING. Left out when it was built because its measured yield was 0:
            # all three `bitwise` states also carried other syntax errors, so clearing the spelling
            # changed nothing. Those blockers have since been cleared by `globals_variant` and
            # `scalar_member_index`, and `alSynSetPan` (1 error) and `alSynSetVol` (2) are now SOLE-
            # blocked by `use of undeclared identifier 'bitwise'`. A zero-yield pass is worth retesting
            # after the frame moves; it is not worth retiring.
            "eval.intake_runners.m2c_dialect",
            # Beside `m2c_dialect` because it is the same kind of fact: an m2c SPELLING that the build's
            # C does not have, lowered to the construct it stands for. `M2C_MEMCPY_ALIGNED` is a bulk
            # copy, and the one thing it must never become is a prototype -- there is no such ROM symbol,
            # so a `jal` to it compiles and moves the state away from exact.
            "eval.intake_runners.m2c_aligned_copy",
            "eval.intake_runners.negative_offset",
            "eval.intake_runners.source_type_declarations",
            "eval.intake_runners.undeclared_identifiers",
            "eval.intake_runners.header_variant",
            "eval.intake_runners.globals_variant",
            # Beside `globals_variant`: both supply a DECLARATION the candidate is missing, one for a
            # datum and one for a called function, and both read clang rather than cfe because cfe stops
            # at the first error and the names would not be in the text.
            "eval.intake_runners.header_prototypes",
            # `implicit_externs` WAS HERE and is withdrawn (LOOP-6). `extern int f();` leaves IDO's object
            # unchanged, but the project's clang policy rejects an UNPROTOTYPED declaration exactly as it
            # rejects an implicit one ("a function declaration without a prototype is deprecated"), so it
            # ADDED an error in 16 states and, on rank ties, those worse candidates were adopted.
            "eval.intake_runners.opaque_variant",
            # AFTER `opaque_variant`, because the shape it rewrites is one `opaque_variant` CREATES: before
            # the parameter is typed, `arg0->unkC` is an unknown type and the OR is not yet a
            # `pointer | int`. Measured: scanning the blocked states for `*(p | n)` without
            # `opaque_variant` finds ZERO; with it, the same states carry the shape.
            "eval.intake_runners.or_address",
            # AFTER every pass that DECLARES something, because it is derived from the declared type:
            # `p->unk10` is only rewritable once `p` has a type, and `opaque_variant`,
            # `source_type_declarations` and `header_variant` are what give it one. Placed here rather
            # than earlier for that reason, not by preference.
            #
            # It owns the frame's largest sole blocker, and it only became visible when the checker
            # stopped being capped at 20 errors: `member-on-typed-pointer` is the ONLY remaining class
            # in 11 states and appears in 74. Measured on those 11 in isolation before wiring --
            # 7 go frontend-clean and 8 compile under IDO, and the 3 that do not are the declines this
            # module makes ON PURPOSE (a `void` base and a `->unk-2`, which have their own owners).
            "eval.intake_runners.scalar_member_index",
            # LAST among the declaration passes, because it edits what they EMIT: a width-derived
            # `extern s32 X;` where the use needs `u16 *`. It is the wall those passes create --
            # `incompatible-int-pointer` became the top sole blocker (65 states, 8 sole) exactly as
            # they started converting states.
            "eval.intake_runners.widen_pointer_declarations",
            "eval.intake_runners.rewrite_do_while",
            # These existing campaign repairs also own residuals in clean intake:
            # target-width void members, then frontend conversion diagnostics.
            "eval.intake_runners.void_members",
            "eval.intake_runners.global_fields",
            "eval.intake_runners.stack_arrays",
            "eval.intake_runners.global_scalars",
            "eval.intake_runners.header_signature",
            "eval.intake_runners.frontend_abi",
            "eval.intake_runners.call_arity",
            "eval.intake_runners.frontend_casts",
            # Clang accepts GNU void-pointer arithmetic that IDO still rejects.
            "eval.intake_runners.ido_byte_cursors")

# The do-while lowering is GATED on the compiler's own words, the same way the campaign gates it: a draft
# that does not contain a do-loop must not be rewritten by a rule that owns that token. Without the gate
# `rewrite_do_while` fired on 17 of 40 states and converted none, which is a decline wearing a fire rate.
DO_WHILE_DIAGNOSTIC = "contains a do-while loop"


# THE CLASSES A RESIDUAL IS SORTED INTO, matched against clang's own wording rather than against the
# repair modules' gate strings. `chain-analysis.json` ranked these from a diagnostic taken at position 2 of
# 7 and got `undeclared-identifier` as the top lever; taken on the candidate the sequence actually produces,
# `member-on-typed-pointer` is. The lesson is in `_diagnostic_chain`: the read has to happen where the
# candidate is, which is why the trace records one per step.
RESIDUAL_CLASSES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("member-on-void", re.compile(r"member reference base type 'void'")),
    ("member-on-scalar-or-array", re.compile(r"member reference base type|not a structure or union")),
    ("non-pointer-subscript", re.compile(r"subscripted value is not an array, pointer, or vector")),
    ("non-pointer-arrow", re.compile(r"member reference type .* is not a pointer")),
    ("array-assignment", re.compile(r"array type .* is not assignable")),
    ("call-arity", re.compile(r"too (?:many|few) arguments to function call")),
    ("invalid-binary-operands", re.compile(r"invalid operands to binary expression")),
    ("not-callable", re.compile(r"called object type .* is not a function")),
    ("incomplete-array-element", re.compile(r"array has incomplete element type")),
    ("missing-prototype", re.compile(r"function declaration without a prototype")),
    ("incompatible-aggregate", re.compile(r"assigning to .* from incompatible type|initializing .* with an expression of incompatible type")),
    # THE CLASS THAT WAS HIDING IN `unclassified`. `source_type_declarations` exists for exactly this
    # diagnostic and the taxonomy had no name for it, so 57 of the 97 error lines behind the frame's
    # `unclassified` bucket were a class the catalog already owns -- reported as a class nobody owns, which
    # is the difference between "we have no repair for this" and "we have one and did not look".
    ("incomplete-definition", re.compile(
        r"incomplete definition of type|field has incomplete type|"
        r"arithmetic on a pointer to an incomplete type")),
    ("aggregate-where-scalar-required", re.compile(
        r"where arithmetic or pointer type is required")),
    ("incompatible-int-pointer", re.compile(
        r"incompatible (?:integer to pointer|pointer to integer) conversion")),
    ("undeclared-member", re.compile(r"no member named|has no member")),
    ("unknown-type-name", re.compile(r"unknown type name")),
    ("undeclared-identifier", re.compile(r"use of undeclared identifier|undeclared identifier")),
    ("undeclared-function", re.compile(r"implicit declaration of function")),
    ("redeclaration/conflict", re.compile(r"redeclaration|conflicting types|previous declaration|"
                                          r"redefinition of")),
    ("incompatible-pointer", re.compile(r"incompatible pointer types")),
    ("parameter-declarator", re.compile(r"expected parameter declarator|type name requires a specifier")),
    ("expected-identifier", re.compile(r"expected identifier")),
    ("other-syntax", re.compile(r"syntax|expected|invalid|illegal", re.I)),
)


def classify_residual(message: str) -> str:
    for label, pattern in RESIDUAL_CLASSES:
        if pattern.search(message):
            return label
    return "unclassified"


def _diagnostic_chain(candidate: str, context, args) -> dict:
    """What clang sees in THIS candidate: the distinct defect classes and how many errors carry them.

    Class sets describe observed operations, not the number of fixes needed.
    Multiple diagnostics can share a cause, and one class can require several
    independent repairs. Read on the current candidate after each change.

    THE CLASS SET IS BUILT FROM EVERY ERROR, NOT FROM THE FIRST FEW. It used to be built from the runner's
    six-line window, which made the class COUNT a sample of one: a state whose first six diagnostics were
    all `undeclared identifier` was reported as a one-class state even when it carried four more classes
    further down. The window is still six for the reader; `max_errors=0` asks the runner for all of them,
    and only the CLASS SET and its histogram are kept here, so a complete observation does not become a
    complete payload.
    """
    from eval.intake_runners import frontend_diagnostics as runner

    result = runner({**context.__dict__, "candidate": candidate}, {"max_errors": 0})
    observation = result.get("observation") or {}
    detail = result.get("detail") or {}
    classes: list[str] = []
    counts: dict[str, int] = {}
    for error in (detail.get("errors") or []):
        found = classify_residual(error.get("what") or "")
        counts[found] = counts.get(found, 0) + 1
        if found not in classes:
            classes.append(found)
    return {"clang": observation.get("status") or result.get("status"),
            "errors": observation.get("error_count", 0),
            "errors_shown": detail.get("errors_shown", len(detail.get("errors") or [])),
            "errors_truncated": bool(detail.get("errors_truncated")),
            # A COMPLETE OBSERVATION, LABELLED -- AND THE LABEL WATCHES THE ERROR LIST, WHICH IS THE ONLY
            # THING THE CLASS SET IS BUILT FROM.
            #
            # This read `diagnostics_truncated` alone, on the reasoning that a cut in the 16 kB of kept text
            # was the only way the list could be short. That was wrong twice over, in OPPOSITE directions,
            # and the frame measured both:
            #
            #   FALSE NEGATIVE, the one that mattered. clang's own `-ferror-limit` stopped the checker at 20
            #   and said so in a `fatal error:` line nothing parsed. Every read in the `globals` receipt is
            #   capped at 19 errors, 352 of 780 sit exactly there, and all 780 are labelled complete.
            #
            #   FALSE POSITIVE, found by fixing the first. With `-ferror-limit=0` the text passes 16 kB on
            #   big residuals, and 83 reads in the `ferrorlimit` receipt were flagged windowed while their
            #   class sets were COMPLETE -- `frontend_diagnostics.analyse` parses `errors` from the whole
            #   output BEFORE trimming the stored text, so a text cut cannot shorten the error list.
            #
            # `errors_truncated` is the right signal: the runner ORs the checker's own answer with its own
            # `max_errors` window, which are the two ways this list can actually be short.
            "classes": classes, "class_counts": counts,
            "classes_from_window": bool(detail.get("errors_truncated")),
            # CARRIED SO THE TWO CUTS CAN BE TOLD APART IN A RECEIPT. Without this the trace recorded a
            # windowed class set and no way to say which cut caused it, which is what made the false
            # positive above take a re-measure to see.
            "diagnostics_truncated": bool(detail.get("diagnostics_truncated")),
            "reason": str(result.get("reason") or "")[:80]}


def gated(label: str, stderr: str) -> bool:
    """Is this action applicable to the CURRENT diagnostics, or only to the state it was written for?"""
    if label.endswith("rewrite_do_while"):
        return DO_WHILE_DIAGNOSTIC in (stderr or "")
    return True


# THE FRAME'S OWN NOISE FLOOR, in two different senses, because conflating them made the first version of
# this metric useless. It reported "20 states, 10%" for a 200-state frame, which is true of comparing a
# FRESH BUILD against anything (membership is re-derived from a live KB, and moved 4 of 40 once) and false
# of comparing two ARMS on one frozen frame -- those run on identical states, so their difference carries
# no membership noise at all. The distinction is the whole reason the frame is frozen before measuring.
#
# THE DRIFT FIGURE IS OBSERVED. `class-frame.json` and `class-frame-r4.json` are two consecutive builds of
# one command: 4 of 40 members differ, while their conversion counts were both 0. Turnover without a rate
# change -- which is why composition is the noise, not the rate.
NOISE_FLOOR_DEFAULT = 4 / 40
MIN_RESOLVABLE_STATES = 2


def resolution(frame_size: int, noise_floor: float = NOISE_FLOOR_DEFAULT) -> dict:
    """The smallest effect this frame can resolve, per comparison kind.

    `across_frames` carries membership drift: a fresh build is a different sample.
    `arms_on_one_frozen_frame` carries none of it, and its floor is the integer minimum instead.
    """
    if frame_size <= 0:
        return {"frame_size": frame_size, "assumed_membership_drift": noise_floor,
                "across_frames": None, "arms_on_one_frozen_frame": None,
                "note": "an empty frame resolves nothing"}
    drift_states = max(MIN_RESOLVABLE_STATES, min(round(frame_size * noise_floor), frame_size))
    frozen_states = min(MIN_RESOLVABLE_STATES, frame_size)
    return {
        "frame_size": frame_size,
        "assumed_membership_drift": round(noise_floor, 4),
        "drift_states_at_this_size": round(frame_size * noise_floor, 1),
        "across_frames": {"resolvable_states": drift_states,
                          "resolvable_percent": round(100.0 * drift_states / frame_size, 2)},
        "arms_on_one_frozen_frame": {"resolvable_states": frozen_states,
                                     "resolvable_percent": round(100.0 * frozen_states / frame_size, 2)},
        "basis": ("membership turnover between two consecutive builds of one command over the 40-state "
                  "frame: 4 of 40 (10%); their conversion counts were 0 and 0"),
        "note": ("compare an arm against another arm with `arms_on_one_frozen_frame` -- they share every "
                 "state. Use `across_frames` only when the two numbers come from different builds, and "
                 "prefer freezing membership and re-running to reporting a drift-sized difference"),
    }


def _placeholder_widths(context, enabled: bool) -> tuple[dict, list]:
    """Binary-derived types for the names m2c left as `?`, or `{}` when the arm is off.

    Empty is not a failure: `eval/binary_types` refuses a location with contradictory or absent accesses
    on purpose, and the caller's `s32` default then applies. The receipt says which names were derived
    and which were left, so "the target agreed" and "the target had nothing to say" stay distinguishable.
    """
    if not enabled:
        return {}, []
    from eval import binary_types
    from solver import m2c_placeholders

    candidate = context.candidate or ""
    names = sorted({name for _offset, name in m2c_placeholders.placeholders(candidate) if name})
    if not names:
        return {}, []
    path = context.target_asm_path
    if not path or not Path(path).exists():
        return {}, [{"name": name, "basis": "no-assembly"} for name in names]
    widths, receipt = binary_types.types_for(Path(path).read_text(encoding="utf-8", errors="replace"),
                                             names)
    return widths, receipt


def rank(verdict: dict | None) -> tuple:
    verdict = verdict or {}
    return (bool(verdict.get("exact")), bool(verdict.get("compiled")),
            float(verdict.get("score") or 0.0))


# THE SIZE STRATA, and they are the project's existing ones (`eval/distance.py`, `eval/faultsearch.py`):
# tiny <20, small <60, medium <150, large <300, huge >=300 bytes. Reusing them means a "medium" here is
# the same medium as in every other report.
TIERS: tuple[tuple[str, int], ...] = (("tiny", 20), ("small", 60), ("medium", 150),
                                      ("large", 300), ("huge", 1 << 62))
TIER_ORDER = tuple(name for name, _ in TIERS)


def tier_of(size: int | None) -> str:
    size = size or 0
    for name, limit in TIERS:
        if size < limit:
            return name
    return TIERS[-1][0]


def candidates(kb: Path, limit: int, *, exclude: set[str]) -> list[str]:
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select f.name from attempts a join functions f on f.addr = a.func_addr "
            "group by f.name having sum(coalesce(a.exact,0)) = 0 "
            "order by max(f.size) asc limit ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [name for (name,) in rows if name not in exclude]


def unsolved_by_tier(kb: Path, *, exclude: set[str]) -> dict[str, list[dict]]:
    """EVERY unsolved function, grouped by size tier, with the failure class the KB recorded.

    THE POOL IS ONE QUERY, NOT A SCAN. The previous frame was the head of `order by size asc`, so with a
    limit of a few dozen it could only ever contain small functions -- the bias this frame exists to
    remove. Selecting from the whole unsolved population and THEN apportioning across tiers is what
    makes the measurement about the entry route instead of about function size.
    """
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    try:
        classes = {addr: (text or "") for addr, text in conn.execute(
            "select func_addr, compiler_stderr from attempts a where a.id = "
            "(select max(id) from attempts where func_addr = a.func_addr)")}
        rows = conn.execute(
            "select f.name, f.addr, min(f.size) as size, count(a.id) as n "
            "from attempts a join functions f on f.addr = a.func_addr "
            "group by f.addr, f.name having sum(coalesce(a.exact,0)) = 0").fetchall()
    finally:
        conn.close()
    out: dict[str, list[dict]] = {name: [] for name in TIER_ORDER}
    for name, addr, size, n in rows:
        if name in exclude:
            continue
        text = classes.get(addr, "")
        out[tier_of(size)].append({
            "function": name, "size": size, "attempts": n, "tier": tier_of(size),
            "failure_class": ("syntax-error" if "Syntax Error" in text else
                              "undeclared-symbol" if "undefined" in text else
                              "selector" if "Selector" in text else
                              "other" if text.strip() else "compiles")})
    # Deterministic and stated: biggest first inside a tier, so a tier's head is its hardest member.
    for entries in out.values():
        entries.sort(key=lambda e: (-(e["size"] or 0), e["function"]))
    return out


def bucket_frame(kb: Path, want: int, *, exclude: set[str], failure_class: str,
                 per_tier: int | None = None) -> tuple[list[dict], dict]:
    """Apportion `want` slots across size tiers, then take the biggest matching member of each tier.

    ROUND ROBIN, ONE PER TIER PER ROUND. A tier that cannot supply its member is recorded as a
    SHORTFALL and the next tier takes the slot, so the frame reaches `want` while the tier composition
    is reported rather than assumed. Under-filling silently would make a rate whose denominator is not
    the requested frame -- the class of defect this probe was rewritten to catch.
    """
    pool = unsolved_by_tier(kb, exclude=exclude)
    matching = {name: [e for e in entries if e["failure_class"] == failure_class]
                for name, entries in pool.items()}
    # RECORD THE STOCK BEFORE SELECTING FROM IT. `matching` is consumed by `.pop(0)` below, so reading its
    # length afterwards reports the leftovers, not the supply -- on the 200-state frame that printed
    # `matching_by_tier: {small: 0, medium: 0, large: 0, huge: 130}` next to `by_tier: {small: 2,
    # medium: 34, large: 57, huge: 107}`, i.e. a report claiming the frame drew from tiers that had
    # nothing while it was drawing 34 from one of them. A composition report nobody can trust is worse
    # than none, because the whole point of this frame is that its composition is stated.
    stock = {name: len(entries) for name, entries in matching.items()}
    quota = per_tier if per_tier is not None else max(1, want // len(TIER_ORDER))
    taken: list[dict] = []
    counts = {name: 0 for name in TIER_ORDER}
    exhausted: list[str] = []
    while len(taken) < want and any(matching[name] for name in TIER_ORDER):
        for name in TIER_ORDER:
            if len(taken) >= want:
                break
            if counts[name] >= quota and any(matching[other] for other in TIER_ORDER
                                             if counts[other] < quota):
                continue                    # this tier is full, and another tier still wants slots
            if not matching[name]:
                if name not in exhausted:
                    exhausted.append(name)
                continue
            taken.append(matching[name].pop(0))
            counts[name] += 1
    allocation = {
        "want": want, "taken": len(taken), "per_tier_quota": quota,
        "by_tier": counts,
        "pool_by_tier": {name: len(entries) for name, entries in pool.items()},
        "matching_by_tier": stock,
        "remaining_by_tier": {name: len(entries) for name, entries in matching.items()},
        "shortfall": want - len(taken),
        "tiers_exhausted": exhausted,
        "failure_class": failure_class,
        "tier_edges": {name: (None if limit > (1 << 60) else limit) for name, limit in TIERS},
    }
    return taken, allocation


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--want", type=int, default=12, help="front-door failures to collect")
    ap.add_argument("--scan", type=int, default=60, help="candidates to examine")
    # A FROZEN FRAME IS MEASURED WHOLE BY DEFAULT. `--want` also capped the replay, so a 40-function
    # frame replayed under the default reported 12 rows -- a rate on a third of the denominator, and
    # exactly the kind of silently-shrunk frame this harness exists to prevent. Only an explicit
    # `--want` caps a replay now.
    ap.add_argument("--frozen-want", type=int, default=0,
                    help="cap a frozen replay (0 = measure the whole frame)")
    ap.add_argument("--split", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/intake-20260921/probe2.json")
    # THE SAMPLE MUST NOT MOVE BETWEEN RUNS. The pool query selects functions with NO exact attempt, so
    # a run that converts two of them removes those two from the next run's pool and quietly measures a
    # different population -- which is exactly what happened between the first two runs of this probe
    # (3 converted / 2 exact, then 2 / 0 on an overlapping-but-different set). Freeze the list once and
    # reuse it; `--frozen` reads a previous payload's function list.
    ap.add_argument("--frozen", type=Path, default=None,
                    help="reuse the function list from a previous probe payload")
    # THE SIZE-BUCKETED FRAME. The frozen frame above is the head of `order by size asc`, so it is
    # mostly small functions and its conversion rate is flattered by them. This builds the frame from
    # the WHOLE unsolved population, apportioned across the project's size tiers, so the rate is about
    # the intake route rather than about function size.
    ap.add_argument("--size-buckets", action="store_true",
                    help="apportion the frame across size tiers instead of taking the smallest first")
    ap.add_argument("--class", dest="failure_class", default="syntax-error",
                    help="the measured failure class the frame is drawn from")
    ap.add_argument("--per-tier", type=int, default=None, help="slots per size tier")
    # `eval/binary_types` derives a placeholder's type from the accesses at the location m2c named it
    # after, and documents that EVERY caller in the pipeline currently passes the `s32` default instead.
    # This arm passes them, so "does the target's own evidence change the answer" is measured rather than
    # assumed. Off by default: it is a second mechanism, not a re-measurement of the first.
    ap.add_argument("--placeholder-widths", action="store_true",
                    help="resolve placeholders with types derived from the target assembly")
    # The membership drift to assume when stating the frame's sensitivity. The default is the drift
    # OBSERVED on the 40-state frame (4 of 40); a caller with a measured figure passes it.
    ap.add_argument("--noise-floor", type=float, default=NOISE_FLOOR_DEFAULT,
                    help="assumed frame-membership drift, as a fraction; sets the resolvable effect size")
    # THE RESIDUAL, READ WHERE THE CANDIDATE IS. One clang call per transforming step per state (about
    # 0.06s each), so the whole trace costs roughly 85s over 200 states. Off by default because the
    # conversion measurement does not need it and the flag's absence should be visible in the payload.
    ap.add_argument("--trace-frontend", action="store_true",
                    help="record clang's defect classes after every step, not only at one fixed point")
    ap.add_argument("--repair-rounds", type=int, default=1,
                    help="opt into bounded alternative composition when greater than one")
    ap.add_argument("--repair-budget", type=int, default=12,
                    help="maximum child compiler calls per composed sequence")
    ap.add_argument("--repair-beam", type=int, default=3,
                    help="retained search candidates, including the protected incumbent")
    ap.add_argument("--seconds", type=float, default=1800.0)
    args = ap.parse_args(argv)
    if args.repair_rounds < 1 or args.repair_budget < 0 or args.repair_beam < 1:
        ap.error("repair rounds/beam must be positive and repair budget nonnegative")

    from eval.intake_runners import RUNNERS
    from eval.tool_agent_run import build_context

    held_out = set()
    if args.split.exists():
        held_out = set(json.loads(args.split.read_text(encoding="utf-8")).get("test") or [])

    conn = sqlite3.connect(str(args.kb))       # read-write: workspace.score logs the attempt
    started, errors = time.time(), []
    rows, per_action, crashed = [], {}, []
    allocation: dict = {}
    try:
        pool = candidates(args.kb, args.scan, exclude=held_out)
        entry_by_name: dict[str, dict] = {}
        if args.frozen:
            previous = json.loads(Path(args.frozen).read_text(encoding="utf-8"))
            frozen_names = [r["function"] for r in previous["rows"]]
            pool = [name for name in frozen_names if name not in held_out]
            entry_by_name = {r["function"]: {"size": r.get("size"), "tier": r.get("tier"),
                                             "failure_class": r.get("failure_class"),
                                             "draft_sha256": r.get("draft_sha256")}
                             for r in previous["rows"]}
        elif args.size_buckets:
            entries, allocation = bucket_frame(args.kb, args.want, exclude=held_out,
                                               failure_class=args.failure_class,
                                               per_tier=args.per_tier)
            pool = [e["function"] for e in entries]
            entry_by_name = {e["function"]: e for e in entries}
            print(json.dumps({"frame": allocation}, indent=2), flush=True)
        examined = 0
        cap = args.want if not args.frozen else (args.frozen_want or len(pool))
        for name in pool:
            if len(rows) >= cap or time.time() - started > args.seconds:
                break
            examined += 1
            try:
                context, why = build_context(args.repo, name, conn=conn)
            except Exception as exc:                            # noqa: BLE001
                errors.append({"function": name, "stage": "build_context",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if context is None:
                errors.append({"function": name, "stage": "build_context", "error": why})
                continue
            initial = dict(context.initial_verdict or {})
            if initial.get("compiled"):
                if args.frozen:
                    # Under a frozen frame a now-compiling function is STILL measured, because the frame
                    # was selected on a state that has since changed. Skipping it would silently shrink
                    # the denominator and inflate the rate.
                    pass
                else:
                    continue                 # not a front-door failure: SELECTED OUT, not counted

            entry = entry_by_name.get(name) or {}
            row = {"function": name, "baseline_score": initial.get("score"),
                   # THE STATE THE FRAME FROZE. A later arm replaying this frame re-derives the draft
                   # from the binary and must be able to show it started from the same bytes; otherwise
                   # "same frame" is an assumption rather than a measurement.
                   "size": entry.get("size"), "tier": entry.get("tier"),
                   "failure_class": entry.get("failure_class") or args.failure_class,
                   "draft_sha256": hashlib.sha256(
                       (context.candidate or "").encode("utf-8")).hexdigest(),
                   "baseline_stderr": (initial.get("stderr") or "")[:120], "actions": {}}
            namespace = {**context.__dict__, "kb_conn": conn}
            baseline_stderr = initial.get("stderr") or ""
            widths, width_receipt = _placeholder_widths(context, args.placeholder_widths)
            row["placeholder_widths"] = sorted(widths)
            row["placeholder_width_receipt"] = width_receipt

            for label in SEQUENCE:
                if not gated(label, baseline_stderr):
                    # NOT APPLICABLE TO THIS STATE, and recorded as such rather than run and counted as a
                    # decline. `rewrite_do_while` owns the `do` token; on a draft the compiler never
                    # complained about, running it and reporting "no-change" would pad its decline count
                    # with states it was never the right answer for.
                    entry = {"changed": False, "status": "gated",
                             "reason": f"the compiler did not report {DO_WHILE_DIAGNOSTIC!r}"}
                    row["actions"][label] = entry
                    stat = per_action.setdefault(label, {"fired": 0, "compiled": 0, "exact": 0,
                                                         "declined": 0, "gated": 0})
                    stat["gated"] = stat.get("gated", 0) + 1
                    continue
                try:
                    result = RUNNERS[label]({**namespace, "widths": widths}, {})
                except Exception as exc:                        # noqa: BLE001
                    # AN ACTION THAT CRASHES IS A PROPERTY OF THE ACTION, NOT A BROKEN HARNESS. The two
                    # were one list, so `globals_variant` raising `ValueError: requires one ordinary
                    # function definition` on a draft m2c wrote stopped the frame being built at all --
                    # and the probe's own rule ("a run with errors is NOT reported as a rate") then read
                    # as "no measurement" when the truth was "one action crashes on this class". The
                    # crash is recorded against the action and counted, and the run stays reportable.
                    entry = {"changed": False, "status": "crashed",
                             "error": f"{type(exc).__name__}: {exc}"}
                    row["actions"][label] = entry
                    stat = per_action.setdefault(label, {"fired": 0, "compiled": 0, "exact": 0,
                                                         "declined": 0, "crashed": 0})
                    stat["crashed"] = stat.get("crashed", 0) + 1
                    crashed.append({"function": name, "stage": label, "action": label,
                                    "error": entry["error"]})
                    continue
                entry = {"changed": bool(result.get("changed")), "status": result.get("status")}
                if result.get("changed") and isinstance(result.get("source"), str):
                    verdict = context.compile_fn(result["source"])
                    entry.update(compiled=bool(verdict.get("compiled")),
                                 exact=bool(verdict.get("exact")), score=verdict.get("score"),
                                 stderr=(verdict.get("stderr") or "")[:120])
                else:
                    entry["reason"] = str(result.get("reason") or "")[:120]
                # THE RUNNER'S DETAIL IS THE RECEIPT, AND IT WAS BEING THROWN AWAY. Rows kept `status`,
                # `changed`, `reason` and the verdict, so everything a runner reports about WHY it did what
                # it did -- the frontend's per-mechanism gate counts, `negative_offset`'s per-use declines,
                # `header_variant`'s added headers -- never reached the payload. A measurement whose bodies
                # are discarded is a count with no way to check it, which is the same shape as the
                # `changed=True` that turned out to mean "deleted the include": true, and unreadable.
                if result.get("detail"):
                    entry["detail"] = result["detail"]
                if result.get("observation") is not None:
                    entry["observation"] = result["observation"]
                row["actions"][label] = entry
                stat = per_action.setdefault(label, {"fired": 0, "compiled": 0, "exact": 0,
                                                     "declined": 0})
                stat["fired"] += int(entry["changed"])
                stat["compiled"] += int(bool(entry.get("compiled")))
                stat["exact"] += int(bool(entry.get("exact")))
                stat["declined"] += int(not entry["changed"])

            # SEQUENTIAL ARM: the campaign's order, best candidate kept, diagnostics refreshed.
            trace_frontend = args.trace_frontend
            trace: list[dict] = []
            current, best, stderr = context.candidate, initial, baseline_stderr
            if trace_frontend:
                trace.append({"after": "start", "changed": True,
                              **_diagnostic_chain(current, context, args)})
            if args.repair_rounds > 1:
                from eval.intake_search import search
                from solver import frontend_diagnostics as frontend
                parent_ids = {hashlib.sha256(current.encode()).hexdigest(): best.get('receipt_id')}
                def score_child(source, parent, action):
                    verdict = context.compile_fn(source, parent_attempt_id=parent_ids.get(parent),
                        action=action, relation='repair', strategy='intake-composition',
                        extra={'parent_source_sha256': parent})
                    parent_ids[hashlib.sha256(source.encode()).hexdigest()] = verdict.get('receipt_id')
                    return verdict
                composed = search({**namespace, "candidate": current, "widths": widths,
                                   "initial_verdict": best}, runners=RUNNERS, sequence=SEQUENCE,
                    score=score_child,
                    observe=lambda source: frontend.analyse(source, repo=Path(context.repo),
                                                            target=context.target),
                    max_rounds=args.repair_rounds, max_attempts=args.repair_budget,
                    beam_width=args.repair_beam,
                    deadline=time.monotonic() + max(0, args.seconds - (time.time() - started)))
                current, best = composed["source"], composed["verdict"]
                stderr = best.get("stderr") or ""
                row["repair_search"] = composed
                crashed.extend({"function": name, "stage": "composed", "action": t["action"],
                                "error": t["reason"]} for t in composed["trace"] if t.get("status") == "crashed")
            for label in (() if args.repair_rounds > 1 else SEQUENCE):
                if time.time() - started > args.seconds:
                    break
                if not gated(label, stderr):
                    continue
                stage_ns = {**context.__dict__, "candidate": current, "kb_conn": conn, "widths": widths,
                            "initial_verdict": {**best, "stderr": stderr}}
                try:
                    result = RUNNERS[label](stage_ns, {})
                except Exception as exc:                        # noqa: BLE001
                    crashed.append({"function": name, "stage": f"seq:{label}", "action": label,
                                    "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if not result.get("changed"):
                    continue
                verdict = context.compile_fn(result["source"])
                if rank(verdict) >= rank(best):
                    current, best = result["source"], verdict
                    stderr = verdict.get("stderr") or ""
                if trace_frontend:
                    trace.append({"after": label.split(".")[-1], "changed": True,
                                  **_diagnostic_chain(current, context, args)})
            # THE THREE LEVELS ARE THREE DIFFERENT CLAIMS AND THE PROBE REPORTED ONE. `compiled` is IDO's
            # exit status on a candidate whose declarations are still wrong enough that clang will not
            # parse it: the object is produced, so the state reads as converted, and nothing downstream can
            # tell it apart from a candidate that is actually well-formed C. The audit's re-read of the
            # recorded rows put 27 states compiling and 16 of those passing the frontend -- the same
            # measurement under two names, and only the second one can be handed to a repair. Read once, on
            # the FINAL candidate, for every row: the read has to happen where the candidate is, and the
            # candidate is the best one.
            final_frontend = _diagnostic_chain(current, context, args)
            if trace_frontend:
                trace.append({"after": "final", "changed": bool(best.get("compiled")),
                              **final_frontend})
                row["diagnostic_trace"] = trace
            row["sequence"] = {"compiled": bool(best.get("compiled")), "exact": bool(best.get("exact")),
                               "score": best.get("score"),
                               # `compiled` is IDO, `frontend` is clang, `exact` is the object
                               # certificate. A row is only a COMPLETED REPAIR at the third level; the
                               # first two are progress, and a rate quoted without them is not a rate.
                               "frontend": final_frontend.get("clang"),
                               "frontend_passed": final_frontend.get("clang") == "passed",
                               "frontend_errors": final_frontend.get("errors"),
                               "frontend_classes": final_frontend.get("classes", []),
                               # THE CANDIDATE'S OWN IDENTITY, so a later stage can find the exact bytes
                               # this measurement scored instead of re-deriving a lookalike. Without it
                               # the only route back to the candidate is re-running the sequence, and a
                               # replay that differs would be substituted silently -- which is the one
                               # thing a dev set drawn from a measurement must not do.
                               "final_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest(),
                               "final_source_chars": len(current)}
            rows.append(row)
            print(json.dumps({"function": name, "tier": row.get("tier"), "size": row.get("size"),
                              "baseline": "does not compile",
                              "fired": [k.split(".")[-1] for k, v in row["actions"].items()
                                        if v.get("changed")],
                              "any_compiled": any(v.get("compiled") for v in row["actions"].values()),
                              "sequence_compiled": row["sequence"]["compiled"],
                              "sequence_frontend": row["sequence"]["frontend"],
                              "sequence_exact": row["sequence"]["exact"]}), flush=True)
    finally:
        conn.close()

    converted = sum(1 for r in rows if r["sequence"]["compiled"])
    # SEPARATED, NOT SUMMED. These three counts answer three questions -- "did IDO accept the bytes",
    # "is the C well-formed", "is the object identical" -- and a table that reports the first as "converted"
    # is the defect the audit found: the headline moved with a number that was not the claim.
    frontend_passed = sum(1 for r in rows if r["sequence"].get("frontend_passed"))
    compiled_and_frontend = sum(1 for r in rows
                                if r["sequence"]["compiled"] and r["sequence"].get("frontend_passed"))
    # UNAVAILABLE IS NOT A REJECTION, here as everywhere else. A checker that could not run says nothing
    # about the candidate, and folding it into "did not pass" would report an unrunnable measurement as a
    # failing one -- the exact confusion `solver/frontend_diagnostics` was written to avoid.
    frontend_status = {
        status: sum(1 for r in rows if (r["sequence"].get("frontend") or "unavailable") == status)
        for status in ("passed", "rejected", "unavailable")}
    # THE FRAME MUST BE THE SIZE IT CLAIMS. `--want` also capped a frozen replay once, so a 40-function
    # frame silently reported 12 rows; a rate over a denominator nobody checked is the defect this whole
    # harness exists to catch. A short frame is recorded as a shortfall, not as a smaller frame.
    expected = len(pool)
    # THE RATE IS REPORTED PER SIZE TIER. A conversion rate that comes only from the tiny tier is not a
    # general lever, and the aggregate cannot say which it is -- that is the whole reason this frame
    # exists, so the breakdown is part of the measurement rather than a figure computed afterwards.
    by_tier = {name: {"n": 0, "converted": 0, "exact": 0, "frontend": 0, "compiled_and_frontend": 0}
               for name in TIER_ORDER}
    for row in rows:
        stat = by_tier.setdefault(row.get("tier") or "unknown",
                                  {"n": 0, "converted": 0, "exact": 0, "frontend": 0,
                                   "compiled_and_frontend": 0})
        stat["n"] += 1
        stat["converted"] += int(bool(row["sequence"]["compiled"]))
        stat["exact"] += int(bool(row["sequence"]["exact"]))
        stat["frontend"] += int(bool(row["sequence"].get("frontend_passed")))
        stat["compiled_and_frontend"] += int(bool(row["sequence"]["compiled"])
                                             and bool(row["sequence"].get("frontend_passed")))
    # THE RESIDUAL ON THE FINAL CANDIDATE, which is what a next action would be chosen against. Counted
    # over distinct classes per state; this is not a distance to compiling.
    class_states: dict[str, int] = {}
    class_errors: dict[str, int] = {}
    for row in rows:
        final = row["sequence"].get("frontend_classes") or []
        for label in set(final):
            class_states[label] = class_states.get(label, 0) + 1
        # AND THE HISTOGRAM ACROSS THE WHOLE SEQUENCE, because a class that only ever appeared at step 3 and
    # was gone by step 7 is a fault the sequence MASKED and then revealed, not one it never faced.
    for entry in (row.get("diagnostic_trace") or []):
        for label, count in (entry.get("class_counts") or {}).items():
            class_errors[label] = class_errors.get(label, 0) + int(count)
    # A STATE'S CLASS SET IS ITS RESIDUE. The distribution of set SIZES is the number that answers "are
    # there only a couple of problems": a frame whose states mostly carry one class is close, and a frame
    # whose states carry five is not, and the two look identical in a per-class total.
    set_sizes: dict[int, int] = {}
    for row in rows:
        size = len(set(row["sequence"].get("frontend_classes") or []))
        set_sizes[size] = set_sizes.get(size, 0) + 1
    # MASKED vs VISIBLE: a class that appears in the trace AFTER step 0 was hidden behind something the
    # sequence repaired. Counted per class, this is what separates "the frontier advanced" from "the edit
    # created a new fault" -- both of which read as "more problems than before".
    revealed: dict[str, int] = {}
    for row in rows:
        trace = row.get("diagnostic_trace") or []
        if len(trace) < 2:
            continue
        first = set(trace[0].get("classes") or [])
        for entry in trace[1:]:
            for label in set(entry.get("classes") or []):
                if label not in first:
                    revealed[label] = revealed.get(label, 0) + 1
                    first.add(label)                    # count each state once per class
    payload = {
        "schema_version": 5, "examined": examined if rows else 0,
        "front_door_failures": len(rows), "errors": errors, "action_crashes": crashed,
        "allocation": allocation, "by_tier": by_tier,
        "per_action": per_action, "rows": rows,
        "sequence_converted": converted,
        "sequence_frontend_passed": frontend_passed,
        "sequence_compiled_and_frontend": compiled_and_frontend,
        "sequence_exact": sum(1 for r in rows if r["sequence"]["exact"]),
        "acceptance": {
            "ido_compiled": converted,
            "ido_compiled_and_frontend_passed": compiled_and_frontend,
            "byte_exact": sum(1 for r in rows if r["sequence"]["exact"]),
            "frontend_status": frontend_status,
            "note": ("three levels of one arm, not three arms. `ido_compiled` is what the object "
                     "comparison could run on; only `byte_exact` is a completed repair, and only "
                     "`ido_compiled_and_frontend_passed` is a candidate that is also well-formed C. "
                     "`frontend_status.unavailable` is neither a pass nor a rejection."),
        },
        "residual_classes_on_final_candidate": dict(sorted(class_states.items(),
                                                           key=lambda kv: -kv[1])),
        # THE HISTOGRAM, WHICH IS THE PART THAT CAN BE FALSIFIED ACROSS ROUNDS. A per-class total cannot
        # answer "did the panel get closer", because a class can shrink while the states carrying it stay
        # stuck; the set-size distribution and the masked/visible split can.
        "fault_histogram": {
            "states": len(rows),
            "classes_on_the_final_candidate": dict(sorted(class_states.items(),
                                                          key=lambda kv: -kv[1])),
            "class_set_sizes": {str(size): set_sizes[size] for size in sorted(set_sizes)},
            "states_with_exactly_one_class": set_sizes.get(1, 0),
            "states_with_no_class": set_sizes.get(0, 0),
            "error_instances_across_the_whole_trace": dict(sorted(class_errors.items(),
                                                                  key=lambda kv: -kv[1])),
            "classes_REVEALED_after_a_repair": dict(sorted(revealed.items(),
                                                           key=lambda kv: -kv[1])),
            "note": ("a class in `classes_REVEALED_after_a_repair` was not visible at the start of the "
                     "sequence and appeared later: it was MASKED, not created. The two read identically in "
                     "a per-class count and mean opposite things for a repair decision. A class count is an "
                     "observation, never a remaining distance."),
        },
        "conversion_rate": round(converted / len(rows), 4) if rows else None,
        "frontend_rate": round(frontend_passed / len(rows), 4) if rows else None,
        "exact_rate": (round(sum(1 for r in rows if r["sequence"]["exact"]) / len(rows), 4)
                       if rows else None),
        "planned_frame": expected, "shortfall": expected - len(rows),
        "resolution": resolution(len(rows), args.noise_floor),
        "seconds": round(time.time() - started, 1),
        "harness_clean": not errors,
        "note": ("the sample is only functions whose m2c draft does NOT compile; firing is not success, "
                 "the compiled verdict of the OUTPUT is. No model was called and nothing was promoted. "
                 "IDO-compiled, frontend-passing and byte-exact are reported separately and are not "
                 "interchangeable."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"front_door_failures": len(rows), "errors": len(errors),
                      "action_crashes": len(crashed),
                      "sequence_converted": converted,
                      "sequence_frontend_passed": frontend_passed,
                      "sequence_compiled_and_frontend": compiled_and_frontend,
                      "sequence_exact": payload["sequence_exact"],
                      "conversion_rate": payload["conversion_rate"],
                      "frontend_rate": payload["frontend_rate"],
                      "by_tier": by_tier,
                      "residual_classes_on_final_candidate":
                          payload["residual_classes_on_final_candidate"],
                      "per_action": {k.split(".")[-1]: v for k, v in per_action.items()},
                      "seconds": payload["seconds"]}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())

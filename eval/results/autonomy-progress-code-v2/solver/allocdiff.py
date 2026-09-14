"""Which WEB is mis-coloured, and what would have to change about it.

Every function in the medium tier that survives layout repair terminates the
same way: a pure register-allocation residual whose substitutions are a uniform
shift up the colour pool. On renderRaceUiSingleTrailEffect the whole thing
reduces to one line,

    -move a3,a0        the target keeps the parameter in a3
    +move a2,a0        we keep it in a2

and the other thirty-one faults follow from it. Permuting source statements
moved the fault count 43 -> 38 and never touched that line, which refuted
statement order as the lever HERE: construction chronology only breaks ties,
and these two webs do not have equal save.

So this module asks the question the perturbation search could not. uopt's
algorithm is quoted, not guessed:

    webs are coloured in descending `save`, lowest-indexed colour first
    save = totalsave / nocs,  nocs = ((n - 2) >> 2) + 2

which makes "why did this value get a2 instead of a3" a computable question.
A web holding a HIGHER-indexed colour in the target than in our build ranked
LOWER there, so some competing web outranked it on save -- we are missing
priority somewhere, not carrying a surplus web. That is the opposite of what
the instruction counts suggested, and it is why the inlining direction was
wrong.

HALF OF THIS WORKED. Read the split before using any of it.

SOUND, and model-free: `mismatches`, `anchor` and `web_instructions` compare
the two streams the oracle already produced and say which value was coloured
differently and where it is referenced. No prediction is involved.

REFUTED: the prescription built on `save`. Seven totalsave models were measured
against real IDO output on the matched corpus, scored on an IDENTICAL set of
802 web pairs that all of them rank strictly. Every one landed between 50.4%
and 58.4% against a 50% chance baseline. (The 65-68% in the per-model table is
an artefact -- a model that discriminates less drops its ties from the
denominator and is graded on an easier subset.)

Sweeping weights did not rescue it because the problem is not the weight
function. Webs here are rebuilt from POST-allocation assembly by splitting at
each register redefinition, which conflates distinct values that reuse a
register and cannot recover values uopt coalesced. uopt.py says the
pre-colouring structure is not visible in compiler output; no weighting of the
wrong objects will rank correctly.

So `prescribe` withholds its options unless a caller passes unvalidated_ok, per
the invariant that a hypothesis does not get to change behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

from solver import diffrepair, uopt


@dataclass
class Mismatch:
    """One web the target and the candidate coloured differently."""
    line: int                    # first instruction of the web, stream index
    target_reg: str
    cand_reg: str
    target_web: uopt.Web
    cand_web: uopt.Web

    @property
    def direction(self) -> str:
        """Which way our web has to move in the save ordering.

        The colour pool is ordered, so a lower index means an earlier rank.
        If the target gave this value a HIGHER-indexed register than we did,
        it ranked later there, and ours is ranked too early.
        """
        t = uopt.COLOR_INDEX.get(self.target_reg, -1)
        c = uopt.COLOR_INDEX.get(self.cand_reg, -1)
        if t < 0 or c < 0 or t == c:
            return "none"
        return "later" if t > c else "earlier"


def align(diff: str) -> tuple[list[uopt.Web], list[uopt.Web]]:
    """Webs of both sides, from the streams the oracle itself compared."""
    target, cand = diffrepair._streams(diff)
    return (uopt.webs("\n".join(target)), uopt.webs("\n".join(cand)))


def mismatches(diff: str) -> list[Mismatch]:
    """Webs that differ in colour, matched by where they are DEFINED.

    Alignment by first-def line is sound precisely where this module applies:
    an allocation-shaped residual has the same instructions in the same order
    on both sides, so instruction index is a stable identity. Where the streams
    diverge structurally the pairing is meaningless, and `applicable` refuses.
    """
    tws, cws = align(diff)

    # SEVERAL webs can begin on one instruction -- `move a3,a0` opens a web for
    # a3 and another for a0 -- so a dict keyed on the first line silently keeps
    # only the last of them and the anchor came back None on exactly the
    # residual this module was built for. Group by first line instead and pair
    # within the group by construction order, which is operand order and is
    # the same on both sides while the streams correspond.
    def grouped(ws: list[uopt.Web]) -> dict[int, list[uopt.Web]]:
        out: dict[int, list[uopt.Web]] = {}
        for w in ws:
            if w.lines:
                out.setdefault(w.lines[0], []).append(w)
        for v in out.values():
            v.sort(key=lambda w: w.number)
        return out

    gt, gc = grouped(tws), grouped(cws)
    out: list[Mismatch] = []
    for line in sorted(set(gt) & set(gc)):
        for t, c in zip(gt[line], gc[line]):
            if t.register == c.register:
                continue
            out.append(Mismatch(line, t.register, c.register, t, c))
    return out


def applicable(diff: str, tolerance: float = 0.9) -> bool:
    """True when the streams line up well enough for index to be an identity.

    Demanding EVERY opcode match refused renderRaceUiSingleTrailEffect, which
    is the function this module was written for: its residual carries two
    structural faults among thirty-six lines, so a handful of positions differ
    while the stream as a whole still corresponds one-to-one. Requiring equal
    LENGTH stays strict -- an inserted or deleted instruction really does
    destroy the index correspondence -- but a few substituted opcodes do not.
    """
    target, cand = diffrepair._streams(diff)
    if not target or not cand or len(target) != len(cand):
        return False
    same = 0
    for a, b in zip(target, cand):
        ma, mb = uopt.INSTR.match(a), uopt.INSTR.match(b)
        if ma and mb and ma.group(1) == mb.group(1):
            same += 1
    return same >= tolerance * len(target)


def anchor(diff: str) -> Mismatch | None:
    """The earliest-defined mis-coloured web.

    Reported on its own because the rest usually follow it: once one value
    takes the wrong colour the whole pool shifts behind it, so thirty-one
    faults can be one decision. Fixing the anchor is the only edit worth
    proposing until it is fixed.
    """
    ms = [m for m in mismatches(diff) if m.direction != "none"]
    return ms[0] if ms else None


def nocs_for(n: int) -> int:
    return ((n - 2) >> 2) + 2 if n >= 2 else 1


def occurrence_targets(web: uopt.Web, want: str,
                       limit: int = 24) -> list[tuple[int, float]]:
    """Occurrence counts that move this web's save the wanted direction.

    `save` is NOT monotone in n: nocs steps at n = 2, 6, 10, 14 ... so adding a
    reference can LOWER save by crossing a step, and removing one can raise it.
    That non-monotonicity is quoted, not assumed, which makes this the exact
    part of the prescription. Returns (n, save) for counts that help, nearest
    first, so a caller can prefer the smallest source change.
    """
    if not web.occurrences:
        return []
    unit = web.weight / web.occurrences          # average weight per reference
    now = web.save
    out: list[tuple[int, float]] = []
    for n in range(1, web.occurrences + limit):
        if n == web.occurrences:
            continue
        save = (unit * n) / nocs_for(n)
        better = save > now if want == "earlier" else save < now
        if better:
            out.append((n, save))
    out.sort(key=lambda ns: abs(ns[0] - web.occurrences))
    return out


def evidence_grade(web: uopt.Web, n: int) -> str:
    """Whether a proposed occurrence count rests on quoted or assumed model.

    nocs = ((n - 2) >> 2) + 2 is quoted from the measurements, so a change that
    crosses a nocs step is predicted by the known part of the algorithm. A
    change that leaves nocs alone moves only `totalsave`, which uopt.py models
    as occurrence weight with loop nesting at 10x and explicitly flags as
    ASSUMED. Saying which one a prescription depends on is the difference
    between a derivation and a guess, and this project has paid for conflating
    them before.
    """
    return "quoted (nocs step)" if nocs_for(n) != web.nocs else "assumed (weight only)"


def web_instructions(diff: str, line: int, limit: int = 8) -> list[tuple[str, str]]:
    """The (target, candidate) instruction pairs belonging to one web.

    A rank number is not actionable on its own -- the edit has to touch the
    VALUE, so the caller needs to see which instructions reference it.
    """
    target, cand = diffrepair._streams(diff)
    _tws, cws = align(diff)
    web = next((w for w in cws if w.lines and w.lines[0] == line
                and w.register in uopt.COLOR_INDEX), None)
    if web is None:
        return []
    out = []
    for i in web.lines[:limit]:
        if i < len(target) and i < len(cand):
            out.append((target[i], cand[i]))
    return out


def prescribe(diff: str, unvalidated_ok: bool = False) -> dict:
    """Name the anchor web and say what would have to change about it.

    REFUTED, AND GATED BECAUSE OF IT. The `save` ordering this depends on was
    measured against real IDO output on the matched corpus, comparing seven
    totalsave models on an IDENTICAL set of 802 web pairs that all of them rank
    strictly. Every model scored between 50.4% and 58.4%. Chance is 50%.

    The 65-68% figures the per-model table reports are an artefact: a model
    that discriminates less excludes its ties from the denominator and is
    scored on an easier subset, so those accuracies are not comparable to each
    other. On equal footing the signal is close to absent.

    The likely cause is structural rather than a bad weight function, which is
    why sweeping weights did not rescue it: webs here are reconstructed from
    POST-allocation assembly by splitting at each register redefinition. That
    conflates distinct values which happen to reuse a register and cannot see
    values uopt coalesced. uopt.py says as much -- its pre-colouring structure
    is not visible in what the compiler emits -- and no weighting of the wrong
    objects will predict the right ranking.

    So `options` is a hypothesis, and CLAUDE.md is explicit that a hypothesis
    does not get to change behaviour. Callers must pass unvalidated_ok to see
    it. What remains sound is everything model-free: `anchor` and
    `web_instructions` are read straight from the diff and say which value is
    mis-coloured and where it is referenced. That is exact, and it is the part
    worth using.
    """
    if not applicable(diff):
        return {"applicable": False, "reason": "streams are not comparable"}
    a = anchor(diff)
    if a is None:
        return {"applicable": True, "anchor": None}
    want = a.direction
    return {
        "applicable": True,
        "anchor": a,
        "line": a.line,
        "target_reg": a.target_reg,
        "cand_reg": a.cand_reg,
        "want": want,
        "occurrences": a.cand_web.occurrences,
        "nocs": a.cand_web.nocs,
        "save": a.cand_web.save,
        "options": (occurrence_targets(a.cand_web, want)[:6]
                    if unvalidated_ok else []),
        "options_withheld": not unvalidated_ok,
        "total_mismatched_webs": len(mismatches(diff)),
    }

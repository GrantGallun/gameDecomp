"""Branch points: the model names WHERE the source has a real choice and WHAT the options are;
deterministic enumeration builds the candidates; the compiler picks.

WHY THIS DIVISION
-----------------
Measured on the campaign (eval/results/refinement-data-20260927/): model-authored edits are ~2x as
productive per attempt as deterministic ones but cost ~35 compiles per call, every winning model
edit was 1-6 lines, and many were spellings the rewrite catalog lacks (commutation on cast-heavy
operands, pointer arithmetic vs indexing, temporaries). Whole-function redrafts from a 20B model
fail to compile about half the time. So one call should buy a NEIGHBOURHOOD, not one candidate:
the model proposes a few local alternatives at the lines the residual implicates, and every one is
compiled. Choosing among spellings is the compiler's job, not the model's.

SELECTION KEEPS ONLY IMPROVING CHILDREN
--------------------------------------
Measured on the campaign's lineage (analysis/flat_split.out, headers stripped): of 106,975 flat
steps, 96,120 were NO-OPS (identical object) and 10,855 NEUTRAL (object changed, same score). The
neutral ones had an exact descendant 0.06% of the time, below improving steps (1.6%); the no-ops'
3.2% is the search carrying on past an equivalent state, not a property of the step. An earlier
version kept flat variants as beam members on the strength of an uncorrected statistic -- its
diff comparison included the timestamped header, so no two diffs were ever equal. Now: exact or
up is kept, flat and no-op are not.

A TREE, NOT A COMBINATION
-------------------------
IDO's register allocation and scheduling are whole-function, so one choice changes the residual
and therefore which lines matter below it. Alternatives proposed against one parent and combined
as if independent never beat the best single on the pilot's canary. So candidates form a tree:
each expanded node gets its OWN branch points from ITS residual. Nodes are objects, not sources
(``object_key``): two spellings that compile identically are one node.

Pure logic only: no compiler, no model. ``eval/redraft_pilot.py`` drives it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from solver import edit_slots

MAX_POINTS = 6
MAX_ALTERNATIVES = 4
MAX_SPAN_LINES = 12

SCHEMA = {
    "type": "object",
    "properties": {
        "branch_points": {
            "type": "array", "minItems": 1, "maxItems": MAX_POINTS,
            "items": {
                "type": "object",
                "properties": {
                    "slot": {"type": "string"},
                    "through": {"type": "string"},
                    "why": {"type": "string"},
                    "alternatives": {"type": "array", "minItems": 1, "maxItems": MAX_ALTERNATIVES,
                                     "items": {"type": "string"}},
                },
                "required": ["slot", "why", "alternatives"],
            },
        },
    },
    "required": ["branch_points"],
}

PROMPT = """\
C below compiles with IDO 5.3 -O2 but not yet to the target MIPS bytes. Your job is NOT to fix it
in one go. Name the places where the SOURCE has a genuine choice of spelling that could change
the generated code, and give the alternative spellings. Every alternative will be compiled and
the compiler will choose; you do not need to know which one is right.

Good branch points are next to the mismatches listed below, for example:
- loop form (for / while / do-while / goto), condition polarity, else-branch order;
- a temporary introduced, removed, or reused; a value cached in a local vs re-read;
- array indexing vs pointer arithmetic; field access vs cast-and-offset;
- operand order of a commutative operation; statement order where independent;
- integer width/signedness of a local or a cast.

Rules:
- Return JSON only: {{"branch_points": [{{"slot": "L12", "through": "L14", "why": "...",
  "alternatives": ["replacement text", ...]}}]}}
- "slot" is a line from the EDITABLE SOURCE SLOTS table; "through" (optional) extends the
  replacement to a later line, at most {max_span} lines in all. Each alternative replaces those
  complete lines. Keep the program's behaviour identical.
- 1 to {max_points} branch points, 1 to {max_alternatives} alternatives each. An alternative must
  differ from the current text. C89. No inline assembly.

TARGET ASSEMBLY (READ-ONLY):
```
{asm}
```

CURRENT C (weighted progress score {score:.3f}):
```c
{code}
```

INSTRUCTION DIFF (READ-ONLY; `-` target, `+` current):
```
{diff}
```

{sites}
{slots}"""


def build_prompt(asm: str, source: str, score: float, diff: str, sites: str,
                 tried: str = "") -> str:
    return PROMPT.format(asm=asm, code=source, score=score, diff=(diff or "")[:10000],
                         sites=sites, slots=edit_slots.render(source),
                         max_span=MAX_SPAN_LINES, max_points=MAX_POINTS,
                         max_alternatives=MAX_ALTERNATIVES) + tried


TRIED_MEANING = {
    "noop": "compiled to the IDENTICAL object -- IDO treats these spellings as the same",
    "down": "compiled, and moved further from the target",
    "not-compiled": "did not compile",
    "transposition": "compiled to an object already explored",
}


def tried_block(results: list[tuple[str, str, str]], limit: int = 30) -> str:
    """Feedback for a re-ask at a node where nothing improved: ``(slot, alternative, kind)``.

    On the first tree canary 25 of 28 alternatives compiled to the identical object: the model's
    notion of a spelling choice is mostly not a choice to IDO. Showing what the compiler ignored
    is the cheapest way to move it toward spellings that change code."""
    rows = [(slot, alt, kind) for slot, alt, kind in results if kind in TRIED_MEANING][:limit]
    if not rows:
        return ""
    lines = ["\nALREADY TRIED AT THIS SOURCE (do not repeat these; propose DIFFERENT choices that "
             "change the generated code):"]
    for slot, alt, kind in rows:
        lines.append(f"- {slot}: {' '.join(alt.split())[:160]}  -> {TRIED_MEANING[kind]}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class BranchPoint:
    slot: str            # short slot name of the first line, e.g. "L12"
    through: str         # short slot name of the last line (== slot for one line)
    start: int           # character span in the PARENT source
    end: int
    current: str
    why: str
    alternatives: tuple[str, ...]


@dataclass
class Parsed:
    points: list[BranchPoint] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)


def _spelling(text: str) -> str:
    """Whitespace cannot change IDO's output, so a respaced line is not an alternative. (Removing
    all whitespace can equate `a - -b` with `a--b`; that only drops an alternative, never compiles
    a wrong one.)"""
    return "".join(text.split())


def _line_number(short: str) -> int | None:
    return int(short[1:]) if short.startswith("L") and short[1:].isdigit() else None


def parse(text: str, source: str) -> Parsed:
    """Validate a model response against the PARENT source. Every rejection is kept with its
    reason, so a response that yields nothing is explained rather than silently empty."""
    out = Parsed()
    try:
        payload = json.loads(text)
        raw_points = payload["branch_points"]
        if not isinstance(raw_points, list):
            raise TypeError("branch_points is not a list")
    except (ValueError, KeyError, TypeError) as exc:
        out.rejects.append({"reason": "unparseable", "detail": str(exc)[:200]})
        return out
    table = {key.split(":", 1)[1]: span for key, span in edit_slots.slots(source).items()}
    for raw in raw_points[:MAX_POINTS]:
        if not isinstance(raw, dict):
            out.rejects.append({"reason": "not-an-object"})
            continue
        slot, through = str(raw.get("slot", "")), str(raw.get("through") or raw.get("slot", ""))
        first, last = _line_number(slot), _line_number(through)
        if slot not in table or through not in table or first is None or last is None:
            out.rejects.append({"reason": "unknown-slot", "slot": slot, "through": through})
            continue
        if last < first or last - first + 1 > MAX_SPAN_LINES:
            out.rejects.append({"reason": "bad-span", "slot": slot, "through": through})
            continue
        start, end = table[slot][0], table[through][1]
        current = source[start:end]
        seen, alternatives = set(), []
        for alt in raw.get("alternatives") or []:
            if not isinstance(alt, str):
                continue
            alt = edit_slots.normalize_newlines(alt).rstrip()
            key = _spelling(alt)
            if not key or key == _spelling(current) or key in seen:
                out.rejects.append({"reason": "empty-identical-or-duplicate", "slot": slot})
                continue
            seen.add(key)
            alternatives.append(alt)
        if alternatives:
            out.points.append(BranchPoint(slot, through, start, end, current,
                                          str(raw.get("why", ""))[:300],
                                          tuple(alternatives[:MAX_ALTERNATIVES])))
    return out


def merge(parsed: list[Parsed]) -> list[BranchPoint]:
    """Union of several responses: same span -> alternatives pooled, duplicates dropped."""
    by_span: dict[tuple[int, int], BranchPoint] = {}
    for result in parsed:
        for point in result.points:
            key = (point.start, point.end)
            if key not in by_span:
                by_span[key] = point
                continue
            old = by_span[key]
            pooled = list(old.alternatives)
            norm = {_spelling(a) for a in pooled}
            for alt in point.alternatives:
                if _spelling(alt) not in norm and len(pooled) < MAX_ALTERNATIVES * 2:
                    pooled.append(alt)
                    norm.add(_spelling(alt))
            by_span[key] = BranchPoint(old.slot, old.through, old.start, old.end, old.current,
                                       old.why, tuple(pooled))
    return sorted(by_span.values(), key=lambda p: p.start)


def diff_body(diff: str) -> str:
    """An instruction diff without its ``---``/``+++`` header lines, which name the dump file and
    carry a timestamp: comparing raw diffs makes every compile look like a different object."""
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return "\n".join(lines)


def object_key(compiled: bool, exact: bool, diff: str) -> str:
    """Identity of a compiled result for the tree's transposition table."""
    import hashlib
    if not compiled:
        return "not-compiled"
    return "exact" if exact else hashlib.sha256(diff_body(diff).encode()).hexdigest()


def overlaps(a: BranchPoint, b: BranchPoint) -> bool:
    return a.start < b.end and b.start < a.end


def apply(source: str, choices: list[tuple[BranchPoint, str]]) -> str:
    """Apply one alternative per chosen point, right to left, on the PARENT source."""
    ordered = sorted(choices, key=lambda c: c[0].start)
    for (left, _), (right, _) in zip(ordered, ordered[1:]):
        if overlaps(left, right):
            raise ValueError(f"branch points {left.slot} and {right.slot} overlap")
    for point, alt in reversed(ordered):
        source = source[:point.start] + alt + source[point.end:]
    return source


@dataclass(frozen=True)
class Outcome:
    point: BranchPoint
    alternative: str
    compiled: bool
    score: float
    exact: bool
    same_object: bool = False   # instruction diff identical to the parent's: a no-op spelling


def classify(outcome: Outcome, parent_score: float) -> str:
    if not outcome.compiled:
        return "not-compiled"
    if outcome.exact:
        return "exact"
    if outcome.score > parent_score:
        return "up"
    if outcome.score < parent_score:
        return "down"
    return "noop" if outcome.same_object else "flat"


def keepers(outcomes: list[Outcome], parent_score: float) -> list[Outcome]:
    """Per branch point, the best alternative that IMPROVED: exact > up. Flat, no-op, down and
    uncompiled alternatives are not kept (see module docstring for the measurement)."""
    rank = {"exact": 3, "up": 2}
    best: dict[tuple[int, int], Outcome] = {}
    for outcome in outcomes:
        kind = classify(outcome, parent_score)
        if kind not in rank:
            continue
        key = (outcome.point.start, outcome.point.end)
        current = best.get(key)
        if current is None or (rank[kind], outcome.score) > (
                rank[classify(current, parent_score)], current.score):
            best[key] = outcome
    return sorted(best.values(), key=lambda o: (-o.score, o.point.start))


def combination_plan(kept: list[Outcome]) -> list[list[Outcome]]:
    """Greedy cumulative sets, best keeper first, skipping any that overlaps one already in the set;
    then every non-overlapping PAIR of the top four keepers. The caller compiles each set and keeps
    the best; a set is only as good as its compile says."""
    plans: list[list[Outcome]] = []
    chosen: list[Outcome] = []
    for outcome in kept:
        if any(overlaps(outcome.point, c.point) for c in chosen):
            continue
        chosen = chosen + [outcome]
        if len(chosen) >= 2:
            plans.append(list(chosen))
    top = kept[:4]
    for i, left in enumerate(top):
        for right in top[i + 1:]:
            pair = [left, right]
            if not overlaps(left.point, right.point) and \
                    not any({id(o) for o in plan} == {id(o) for o in pair} for plan in plans):
                plans.append(pair)
    return plans


# Measured on the branch-point pilot (eval/results/refinement-data-20260927/analysis/noop_kinds.out):
# the share of compiled alternatives, by the kind of change the model named, that produced the
# parent's object anyway. The stage probe (ido_stage_probe.py) shows where they die: casts and
# index/pointer spellings in IDO's front end, loop spellings and commuted operands in the optimizer.
STEER = """
WHAT IDO IGNORES AND WHAT IT RESPONDS TO (measured on this project; share of proposals that
changed nothing at all):
- IGNORED, avoid proposing: casts / type spelling (80% no change), array index vs pointer
  arithmetic (84%), loop spelling for / while / do-while (88%), temporaries that only rename a
  value (75%).
- RESPONDS, prefer these: order of independent statements and of operands (50% change the code),
  signedness / width of locals and loads (40%), branch conditions and their polarity (37%),
  genuinely different control structure (a new loop or branch, not a respelling).
"""

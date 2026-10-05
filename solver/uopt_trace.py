"""Parse uopt's own register-allocation trace and check the colouring model against it.

HYP-20260912-01 showed that IDO's register choice cannot be recovered from the
finished assembly: most webs see a dozen free registers and nothing in the
output says what constrained the choice. uopt prints that state itself under
`-zdbug:5` and `-zdbug:6`. Stock ido-static-recomp v1.0 aborted in the
unimplemented `ecvt` wrapper the first time the trace printed a float, so the
priorities were never seen; `tools/ido-trace/` carries the fix and the gate that
shows the patched compiler reproduces the SBK1 ROM.

What each level prints (uoptlist, written to the compiler's working directory):

    -zdbug:6  one line per colouring decision, in the order uopt made them,
              followed by the procedure's "global coloring of NAME" timing line:
                 58:   58 assigned (unconstrained)   2
                 80:   80 not colored (-ve save)
              live range  51:   51 split out  103
                 51:  103 assigned (constrained)   7
    -zdbug:5  the "global coloring of NAME" line, then one record per live
              range after colouring:
              >>>active<<<{848|0}   58   2          node, live range, colour (-1 none)
              adjsave, hasstore: 2.00000000e+00  true
              forbidden: [  1  3  4  5]             colours unavailable when it was coloured
              :::interfere with:::   99   98 ...
              - live bb -   0  1  0  4              block, two uninterpreted counts, preferred colour
              - live bb (default) (  1) [ 1, 3.. 4] blocks with a default row

The dump is observation: it is what the compiler did to one source. `check`
compares it with the allocation model and labels each rule as observed or
inferred; nothing here writes the knowledge base.

THE SELECTION MODEL (`select_colour`), fitted on the SBK1 census (in-sample:
11,204 integer and 106 float decisions; see eval/results/uopt-trace-20260914):

    1. a preferred colour (last column of a block row) inside the band: the first
       one not forbidden, or scanning upward from the first when all are
    2. otherwise a parameter live in block 0 scans upward from the band's first
       argument colour (a0 = 3 for integers, 26 for floats)
    3. otherwise the lowest colour not forbidden in the band

The band is NOT predicted: whether a range must survive a call is not printed, so
it is read from the colour uopt chose. The model says which colour inside that band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# (first, end, argument start) per band. Colour 23 took integer ranges whose 1-22 were all
# forbidden, so the integer callee-saved band ends at 24; 30+ were float callee-saved.
BANDS = {"int_caller": (1, 14, 3), "int_callee": (14, 24, 14),
         "float_caller": (24, 30, 26), "float_callee": (30, 64, 30)}

_TIMING = re.compile(r"SECONDS IN global coloring of (\S+)")
_ACTIVE = re.compile(r">>>active<<<\{\s*(\d+)\|\s*(\d+)\}\s+(-?\d+)\s+(-?\d+)")
_ADJSAVE = re.compile(r"adjsave, hasstore:\s*(\S+)\s+(true|false)")
_ASSIGNED = re.compile(r"^\s*(\d+):\s+(\d+) assigned \((constrained|unconstrained)\)\s+(-?\d+)")
_NOT_COLORED = re.compile(r"^\s*(\d+):\s+(\d+) not colored \(([^)]*)\)")
_SPLIT = re.compile(r"live range\s+(\d+):\s+(\d+) split out\s+(\d+)")
_INTS = re.compile(r"^[\s\d]+$")
_LIVE_BLOCK = re.compile(r"^- live bb -\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
_LIVE_DEFAULT = re.compile(r"^- live bb \(default\) \(\s*\d+\)\s*(\[.*\])")
_BLOCK_FLAGS = re.compile(r"^firstisstr deadout needreglod needregsave\s+(true|false)\s+(true|false)\s+(true|false)\s+"
                          r"(true|false)")
# `{1085|0}   0 isvar   P   41   0vreg`: node, listing position, kind (M local, P parameter,
# R return), block, frame offset. On the SBK1 census the position equalled the node's
# live-range number for 98.4% of isvar lines but only 48% of isop lines; join on node.
_ISVAR = re.compile(r"^\{\s*(\d+)\|\s*\d+\}\s+\d+\s+isvar\s+([A-Z])\s+\d+\s+(-?\d+)")


def parse_set(text: str) -> frozenset[int]:
    """Pascal set notation: `[  1  3..   5,   7]` -> {1, 3, 4, 5, 7}."""
    body = re.sub(r"\s*\.\.\s*", "..", text.strip().strip("[]"))
    values: set[int] = set()
    for part in re.split(r"[,\s]+", body):
        if ".." in part:
            low, high = (int(x) for x in part.split(".."))
            values.update(range(low, high + 1))
        elif part:
            values.add(int(part))
    return frozenset(values)


def parse_real(token: str) -> float:
    """A Pascal real as uopt writes it, including non-finite values.

    A NaN priority prints as `-.nan\\x000000e-01`: the host ecvt returns "nan", and
    IRIX's formatter copies its fixed nine digits past the terminator. Only the
    non-finite marker is meaningful; the bytes after it are stale buffer contents.
    """
    lowered = token.lower()
    if "nan" in lowered:
        return float("nan")
    if "inf" in lowered:
        return float("-inf") if lowered.startswith("-") else float("inf")
    return float(token)


@dataclass
class LiveRange:
    lr: int
    node: int
    color: int                     # -1: not coloured
    adjsave: float | None = None
    hasstore: bool | None = None
    forbidden: frozenset[int] | None = None
    interferes: tuple[int, ...] = ()
    constant: bool = False
    kind: str | None = None        # isvar kind of the range's node: M, P, R; None for expressions
    offset: int | None = None
    blocks: tuple[tuple[int, int, int, int], ...] = ()   # (block, ?, ?, preferred colour) rows
    default_blocks: frozenset[int] = frozenset()
    # block -> (firstisstr, deadout, needreglod, needregsave), from the line after each block row
    block_flags: dict[int, tuple[bool, bool, bool, bool]] = field(default_factory=dict)

    def preferences(self) -> list[int]:
        """Non-zero preferred colours in block-row order, without repeats."""
        return list(dict.fromkeys(row[3] for row in self.blocks if row[3] > 0))

    def live_at_entry(self) -> bool:
        return any(row[0] == 0 for row in self.blocks) or 0 in self.default_blocks


@dataclass
class Decision:
    lr: int                        # the live range as numbered before any split
    piece: int                     # the range actually coloured (differs after a split)
    outcome: str                   # constrained | unconstrained | not_colored
    color: int = -1
    reason: str = ""


@dataclass
class Procedure:
    name: str
    ranges: dict[int, LiveRange] = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    splits: list[tuple[int, int]] = field(default_factory=list)


def parse_level5(text: str) -> dict[str, dict[int, LiveRange]]:
    procedures: dict[str, dict[int, LiveRange]] = {}
    current: dict[int, LiveRange] | None = None
    record: LiveRange | None = None
    in_interfere = pending_const = False
    variables: dict[int, tuple[str, int]] = {}   # listed before the timing line they belong to
    upcoming: dict[int, tuple[str, int]] = {}
    for line in text.splitlines():
        isvar = _ISVAR.match(line)
        if isvar:
            upcoming[int(isvar.group(1))] = (isvar.group(2), int(isvar.group(3)))
            continue
        timing = _TIMING.search(line)
        if timing:
            current = procedures.setdefault(timing.group(1), {})
            variables, upcoming = upcoming, {}
            record, in_interfere = None, False
            continue
        if current is None:
            continue
        active = _ACTIVE.search(line)
        if active:
            node, _, lr, color = (int(x) for x in active.groups())
            kind, offset = variables.get(node, (None, None))
            record = current[lr] = LiveRange(lr=lr, node=node, color=color, kind=kind, offset=offset)
            in_interfere = pending_const = False
            continue
        if record is None:
            continue
        if line.startswith("% % % node") or "SECONDS IN" in line:
            record, in_interfere = None, False
            continue
        if line.startswith("const------>"):
            record.constant, pending_const = True, True
            continue
        if pending_const:                 # the constant's value line
            pending_const = False
            continue
        adjsave = _ADJSAVE.search(line)
        if adjsave:
            record.adjsave, record.hasstore = parse_real(adjsave.group(1)), adjsave.group(2) == "true"
            in_interfere = False
        elif line.startswith("forbidden:"):
            record.forbidden = parse_set(line.split(":", 1)[1])
            in_interfere = False
        elif line.startswith(":::interfere with:::"):
            record.interferes = tuple(int(x) for x in line.split(":::")[-1].split())
            in_interfere = True
        elif in_interfere and _INTS.match(line) and line.strip():
            record.interferes += tuple(int(x) for x in line.split())   # wrapped list
        elif (block := _LIVE_BLOCK.match(line)):
            record.blocks += (tuple(int(x) for x in block.groups()),)
            in_interfere = False
        elif (flags := _BLOCK_FLAGS.match(line)) and record.blocks:
            record.block_flags[record.blocks[-1][0]] = tuple(value == "true" for value in flags.groups())
        elif (default := _LIVE_DEFAULT.match(line)):
            record.default_blocks = record.default_blocks | parse_set(default.group(1))
            in_interfere = False
        else:
            in_interfere = False
    return procedures


def parse_level6(text: str) -> dict[str, tuple[list[Decision], list[tuple[int, int]]]]:
    procedures: dict[str, tuple[list[Decision], list[tuple[int, int]]]] = {}
    decisions: list[Decision] = []
    splits: list[tuple[int, int]] = []
    for line in text.splitlines():
        timing = _TIMING.search(line)
        if timing:
            procedures[timing.group(1)] = (decisions, splits)
            decisions, splits = [], []
            continue
        split = _SPLIT.search(line)
        if split:
            splits.append((int(split.group(2)), int(split.group(3))))
            continue
        assigned = _ASSIGNED.match(line)
        if assigned:
            lr, piece, kind, color = assigned.groups()
            decisions.append(Decision(int(lr), int(piece), kind, int(color)))
            continue
        missed = _NOT_COLORED.match(line)
        if missed:
            decisions.append(Decision(int(missed.group(1)), int(missed.group(2)), "not_colored",
                                      reason=missed.group(3)))
    return procedures


def join(level5: str, level6: str) -> dict[str, Procedure]:
    """One Procedure per function present in both traces of the same compile."""
    ranges, decided = parse_level5(level5), parse_level6(level6)
    joined = {}
    for name in ranges.keys() & decided.keys():
        decisions, splits = decided[name]
        joined[name] = Procedure(name, ranges[name], decisions, splits)
    return joined


def band_of(color: int) -> str | None:
    for name, (first, end, _argument) in BANDS.items():
        if first <= color < end:
            return name
    return None


def _scan(start: int, end: int, forbidden: frozenset[int]) -> int | None:
    return next((c for c in range(start, end) if c not in forbidden), None)


def select_colour(record: LiveRange, band: str) -> int | None:
    """The colour the selection model gives `record` inside `band` (see module docstring)."""
    first, end, argument = BANDS[band]
    forbidden = record.forbidden or frozenset()
    preferred = [p for p in record.preferences() if first <= p < end]
    if preferred:
        free = [p for p in preferred if p not in forbidden]
        return free[0] if free else _scan(preferred[0], end, forbidden)
    if record.kind == "P" and record.live_at_entry():
        return _scan(argument, end, forbidden)
    return _scan(first, end, forbidden)


def check(proc: Procedure) -> dict:
    """Compare one procedure's recorded decisions with the allocation model.

    observed  colour_mismatch      level-6 colour == level-5 colour for the same range
    observed  forbidden_not_at_time  forbidden holds every earlier-coloured neighbour's colour
    model     selection_miss       colour == select_colour(range, band of the chosen colour)
    model     order                constrained ranges first, in non-increasing adjsave; then
                                   unconstrained ranges in increasing live-range number
    """
    report = {"function": proc.name, "decisions": 0, "colour_mismatch": [], "missing_record": [],
              "forbidden_not_at_time": [], "selection_miss": [], "order": [], "nonfinite_adjsave": []}
    coloured: dict[int, int] = {}
    last_kind, last_adjsave, last_lr = None, None, -1
    for decision in proc.decisions:
        if decision.outcome == "not_colored":
            continue
        report["decisions"] += 1
        record = proc.ranges.get(decision.piece)
        if record is None:
            report["missing_record"].append(decision.piece)
            coloured[decision.piece] = decision.color
            continue
        if record.color != decision.color:
            report["colour_mismatch"].append((decision.piece, decision.color, record.color))
        forbidden = record.forbidden or frozenset()
        earlier = {coloured[n] for n in record.interferes if n in coloured and coloured[n] > 0}
        if not earlier <= forbidden:
            report["forbidden_not_at_time"].append((decision.piece, sorted(earlier - forbidden)))
        band = band_of(decision.color)
        predicted = select_colour(record, band) if band else None
        if predicted != decision.color:
            report["selection_miss"].append((decision.piece, decision.color, predicted))
        if decision.outcome == "constrained":
            adjsave = record.adjsave
            if adjsave is not None and adjsave != adjsave:     # NaN compares false: never let it pass silently
                report["nonfinite_adjsave"].append(decision.piece)
                adjsave = None
            if last_kind == "unconstrained":
                report["order"].append((decision.piece, "constrained after unconstrained"))
            elif last_adjsave is not None and adjsave is not None and adjsave > last_adjsave:
                report["order"].append((decision.piece, f"adjsave {adjsave} after {last_adjsave}"))
            last_adjsave = adjsave if adjsave is not None else last_adjsave
        else:
            if last_kind == "unconstrained" and decision.piece < last_lr:
                report["order"].append((decision.piece, f"lr {decision.piece} after {last_lr}"))
            last_lr = decision.piece
        last_kind = decision.outcome
        coloured[decision.piece] = decision.color
    report["consistent"] = not any(report[k] for k in ("colour_mismatch", "missing_record",
                                                         "forbidden_not_at_time", "selection_miss", "order"))
    return report

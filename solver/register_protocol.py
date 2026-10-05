"""Register protocol: for each wrongly coloured live range, which levers can reach the target register.

`uopt_diagnosis` says WHICH range holds the wrong register and classifies it. This module says what
could change it, using only how uopt chooses a colour, and says when nothing can.

How a colour is chosen (IDO 5.3 trace model, 99.9% of 11,310 SBK1 decisions; mechanism read from the
IDO 7.1 uopt decompile, n64decomp/ido src/uopt/uoptreg1.c, where live units get `lu->reg`):

  order      constrained ranges by priority (adjsave), then unconstrained by live-range number
  preference a live unit's preferred register is set only when, in that basic block, the value is
             (a) a parameter at entry, (b) an out-mode parameter at exit, (c) stored to an outgoing
             argument, or (d) passed as call argument N (register a0+N-1). Nothing else sets one.
  selection  first free preferred colour; else a parameter live at entry scans up from a0; else
             the lowest colour not forbidden by an earlier-coloured interfering range

So, with the instruction stream already matching, each lever is checkable against the target:

  preference     possible only if a call in a block where the range lives passes the desired
                 argument register (the block's `regsused` set in the trace says which it claims)
  forbid lower   possible only if every free colour below the desired one is visibly held in the
                 TARGET during the range's span: the overlapping values already exist, only their
                 order or overlap differs
  parameter      possible only for an argument register the scan from a0 would land on
  coalesce       another range that does not interfere already holds the desired colour: making both
                 values one variable gives this one that colour. Found by running this module: its
                 first census called stepRaceMotionLoopingAnimation unreachable although scalar
                 coalescing had made it exact (catalog ordered-scalar-temporaries-can-split-...)
  inline         the desired register is a ugen FIFO temporary, so the value may need to be an
                 expression temporary rather than a coloured variable
  priority       a blocked range whose owner is a variable can be raised (`if (!x);`, catalog
                 uopt53-*); interference can be shortened by re-reading or reordering
  ugen / split   temporary count and order, or the control structure around the range

A range with every lever impossible is UNREACHABLE: no register-level edit that keeps these
instructions can produce the target colour, so the instruction-level structure is what differs
(the same instructions from different C). That verdict is the point: it stops register search from
spending compiles where it cannot succeed. It is a model verdict, not a proof about all C.
"""
from __future__ import annotations

import difflib
import re

from solver import regalloc_signature, uopt_attribution, uopt_diagnosis, uopt_trace

_TIMING = re.compile(r"SECONDS IN global coloring of (\S+)")
_NODE = re.compile(r"^% % % node\s+(\d+)")
_REGSUSED = re.compile(r"^regsused\[1\]:\s*\[([^\]]*)\]")
# ugen's FIFO free list (IDO 7.1 ugen reg_mgr, confirmed on 5.3: eval/results/ugen-temps-20260923)
UGEN_TEMPS = ("t6", "t7", "t8", "t9", "t0", "t1", "t2", "t3", "t4", "t5")


def node_regsused(level5: str, function: str) -> dict[int, frozenset[int]]:
    """Integer colours each basic block claims for fixed purposes (call arguments, parameters, returns)."""
    section, inside = [], False
    for line in level5.splitlines():
        found = _TIMING.search(line)
        if found:
            if inside:
                break
            inside = found.group(1) == function
            continue
        if inside:
            section.append(line)
    out: dict[int, frozenset[int]] = {}
    node = None
    for line in section:
        m = _NODE.match(line.strip())
        if m:
            node = int(m.group(1))
            continue
        m = _REGSUSED.match(line.strip())
        if m and node is not None:
            out[node] = frozenset(int(x) for x in m.group(1).split())
    return out


def _colour_of(register: str) -> int | None:
    return uopt_attribution.REGISTER_COLOUR.get(uopt_attribution.REGISTER_NUMBER.get(register))


def _name(colour: int) -> str:
    number = uopt_attribution.colour_register(colour)
    return uopt_attribution.REGISTER_NAMES[number] if number is not None else f"colour{colour}"


def _target_registers_by_candidate(target_dump: str, candidate_dump: str) -> dict[int, set[str]]:
    """Registers each aligned TARGET instruction uses, keyed by candidate instruction index."""
    target = regalloc_signature.parse(uopt_attribution.strip_padding(target_dump))
    candidate = regalloc_signature.parse(uopt_attribution.strip_padding(candidate_dump))
    out: dict[int, set[str]] = {}
    matcher = difflib.SequenceMatcher(a=[i.shape() for i in target], b=[i.shape() for i in candidate],
                                      autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            for k in range(a1 - a0):
                out[b0 + k] = {reg for _pos, reg in target[a0 + k].registers()}
    return out


def _levers(entry: dict, record, proc, regsused, span_regs: set[str]) -> list[dict]:
    kind = entry["class"]
    levers: list[dict] = []
    if kind == "selection":
        desired = _colour_of(entry["desired"])
        band = uopt_trace.band_of(record.color)
        first, end, argument = uopt_trace.BANDS[band] if band else (1, 14, 3)
        if desired is None or not first <= desired < end:
            levers.append({"lever": "band", "status": "possible",
                           "why": "desired register is in another save band; crossing calls decides it"})
            return levers
        blocks = sorted({row[0] for row in record.blocks} | set(record.default_blocks))
        claiming = [b for b in blocks if desired in regsused.get(b, frozenset())]
        levers.append({"lever": "preference",
                       "status": "available" if claiming else "impossible",
                       "why": (f"block(s) {claiming} claim {_name(desired)} (a call argument or parameter there)"
                               if claiming else
                               f"no block where the range lives ({blocks}) claims {_name(desired)}; "
                               "a preference needs the value passed in that register there")})
        forbidden = record.forbidden or frozenset()
        lower = [c for c in range(first, desired) if c not in forbidden]
        missing = [_name(c) for c in lower if _name(c) not in span_regs]
        levers.append({"lever": "forbid-lower",
                       "status": "available" if lower and not missing else ("impossible" if missing else "n/a"),
                       "why": (f"target holds {', '.join(_name(c) for c in lower)} during the range"
                               if lower and not missing else
                               f"target shows no value in {', '.join(missing)} during the range" if missing
                               else "no free colour below the desired one")})
        partners = [n for n, other in proc.ranges.items()
                    if n != entry["lr"] and other.color == desired and n not in record.interferes
                    and entry["lr"] not in other.interferes]
        levers.append({"lever": "coalesce", "status": "available" if partners else "impossible",
                       "why": (f"non-interfering range(s) {partners} already hold {_name(desired)}; one variable "
                               "for both values takes its colour" if partners else
                               f"no non-interfering range holds {_name(desired)}")})
        if entry["desired"] in UGEN_TEMPS:
            levers.append({"lever": "inline", "status": "possible",
                           "why": f"{entry['desired']} is also a code-generator temporary; the value may be an "
                                  "expression temp rather than a variable"})
        scan = uopt_trace._scan(argument, end, forbidden)
        has_params = any(r.kind == "P" for r in proc.ranges.values())
        levers.append({"lever": "parameter",
                       "status": "possible" if scan == desired and record.kind != "P" and has_params else "impossible",
                       "why": ("the scan from a0 lands on the desired register; needs the value in a "
                               "parameter-kind variable live at entry" if scan == desired and has_params else
                               f"the scan from a0 would give {_name(scan) if scan else 'nothing'}")})
    elif kind == "blocked":
        variable = record.kind is not None and not record.constant
        levers.append({"lever": "priority", "status": "possible" if variable else "impossible",
                       "why": ("raise this variable's priority above its blockers "
                               f"{entry.get('blockers')} (extra read, earlier first store)" if variable else
                               "the range is an expression or constant; there is no variable to re-read")})
        levers.append({"lever": "interference", "status": "possible",
                       "why": "shorten the overlap with the blockers (re-read, reorder, rematerialise)"})
    elif kind == "ugen_temp":
        levers.append({"lever": "temporaries", "status": "possible",
                       "why": "the code generator's FIFO temporaries; number and order of expression temps"})
    elif kind == "split":
        levers.append({"lever": "structure", "status": "possible",
                       "why": "the range was split; loop and branch structure around it decide the pieces"})
    return levers


def verdict_of(levers: list[dict]) -> str:
    statuses = {l["status"] for l in levers}
    if "available" in statuses:
        return "reachable"
    if "possible" in statuses:
        return "conditional"
    return "unreachable"


def analyse(target_dump: str, candidate_dump: str, level5: str, level6: str, ugen_dump: str,
            function: str) -> dict:
    report = uopt_diagnosis.diagnose(target_dump, candidate_dump, level5, level6, ugen_dump, function)
    if report.get("declined"):
        return {"function": function, "verdict": "declined", "reason": report["declined"]}
    if report.get("non_register"):
        return {"function": function, "verdict": "not-register-only", "non_register": report["non_register"]}
    proc = uopt_trace.join(level5, level6)[function]
    regsused = node_regsused(level5, function)
    attribution = uopt_attribution.attribute(candidate_dump, level5, level6, ugen_dump, function)
    by_candidate = _target_registers_by_candidate(target_dump, candidate_dump)
    ranges = []
    for entry in report["ranges"]:
        if entry["class"] == "ok":
            continue
        record = proc.ranges[entry["lr"]]
        positions = [index for index, rows in attribution.operands.items()
                     if any(lr == entry["lr"] for _p, _r, lr in rows)]
        span_regs: set[str] = set()
        if positions:
            for index in range(min(positions), max(positions) + 1):
                span_regs |= by_candidate.get(index, set())
        levers = _levers(entry, record, proc, regsused, span_regs)
        ranges.append({"lr": entry["lr"], "class": entry["class"], "actual": entry["actual"],
                       "desired": entry.get("desired"), "constant": entry.get("constant"),
                       "kind": entry.get("kind"), "verdict": verdict_of(levers), "levers": levers})
    verdicts = {r["verdict"] for r in ranges}
    function_verdict = ("unreachable" if "unreachable" in verdicts else
                        "reachable" if verdicts <= {"reachable"} and verdicts else
                        "conditional" if ranges else "no-wrong-range")
    return {"function": function, "verdict": function_verdict, "ranges": ranges,
            "unattributed": report.get("unattributed"), "regsused": {k: sorted(v) for k, v in regsused.items()}}

"""Measured gates: contexts where a mutation family is skipped because recorded searches show it never pays.

Gates are data, not judgement. `eval.mechanism_roadmap` learns them from recorded search worlds: a
(family, context) cell is gated only when, cross-fitted by function halves, it produced no exact child and
almost no improving child, and gating it on the held-out half lost no exact and at most 1% of improving edges.
They are installed into `family_gates.json` beside this module only after a population rerun with them
lost no exact (see that file's provenance). A gate only removes candidates; it never adds or reorders one.

Context comes from the parent's verdict, so gating needs `evidence`; without it nothing is gated:
  state   bytes_exact (compiled, empty diff) | frontend_rejected | residual
  recipe  O1 | O2 | unknown                   (the recipe's C_OPT; libultra builds at -O1, where uopt does
                                               not run. COMPILING_LIBULTRA is defined for game code too, so
                                               it cannot tell them apart: 193 of 193 SBK1 compiles carry it)
  axis    dominant solver.signals residual axis of the diff, or none
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

GATES = Path(__file__).with_name("family_gates.json")
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def context(diff: str, evidence: dict | None) -> dict[str, str] | None:
    if not evidence:
        return None
    from solver import signals
    frontend = evidence.get("frontend") or {}
    c_opt = (((evidence.get("compiler_recipe") or {}).get("settings") or {}).get("C_OPT") or "").strip()
    if evidence.get("compiled") and not diff:
        state = "bytes_exact"
    elif frontend.get("passed") is False:
        state = "frontend_rejected"
    else:
        state = "residual"
    axis = "none"
    if diff:
        s = signals.analyse(diff, float(evidence.get("score") or 0.0), False, True)
        counts = {a: int(getattr(s, a)) for a in AXES}
        if any(counts.values()):
            axis = max(sorted(counts), key=lambda a: counts[a])
    return {"state": state, "recipe": c_opt.lstrip("-") or "unknown", "axis": axis}


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> frozenset:
    data = json.loads(Path(path).read_text())
    return frozenset((g["family"], g["feature"], g["value"]) for g in data.get("gates", []))


def gated(kinds, ctx: dict[str, str] | None, path: Path | None = None) -> bool:
    """True when every kind of a family is gated in this context."""
    path = path or GATES
    if ctx is None or not path.exists():
        return False
    gates = _load(str(path), path.stat().st_mtime)
    return all(any((kind, f, v) in gates for f, v in ctx.items()) for kind in kinds)

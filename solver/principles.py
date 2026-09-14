"""Retrieve compiler principles from a target/candidate residual.

Confirmed catalog entries are admissible solver knowledge.  Experimental
hypotheses are separately labelled and may be enabled only by an experiment;
they never silently become production rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from patterns.catalog import CATALOG, Pattern
from solver import residual, workspace


@dataclass(frozen=True)
class Match:
    pattern_id: str
    status: str
    reason: str
    guidance: str

    def render(self) -> str:
        return (
            f"[{self.status}] {self.pattern_id}\n"
            f"Activation: {self.reason}\n"
            f"Guidance: {self.guidance}"
        )


def _from_pattern(pattern: Pattern, reason: str) -> Match:
    status = "CONFIRMED" if pattern.confirmed_on else "EXPERIMENTAL HYPOTHESIS"
    return Match(pattern.id, status, reason, pattern.prescription)


def _isolated_register_web(packet: residual.ResidualPacket) -> bool:
    faults = packet.faults
    return (
        packet.compiled
        and not packet.exact
        and packet.instruction_delta == 0
        and packet.text_length_delta == 0
        and faults.get("register_allocation", 0) > 0
        and all(count == 0 for kind, count in faults.items()
                if kind != "register_allocation")
    )


def retrieve(target_asm: str, attempt: workspace.Attempt,
             packet: residual.ResidualPacket, *,
             include_hypotheses: bool = False,
             limit: int = 5) -> tuple[Match, ...]:
    """Return mechanically activated principles, confirmed entries first."""
    matches: list[Match] = []
    diff = attempt.diff or ""

    # Candidate-residual activations for catalog entries whose original
    # detector cannot be target-only.
    if packet.faults.get("relocation", 0):
        matches.append(_from_pattern(
            CATALOG["relocation-mismatch"],
            "the residual classifier reports relocation differences"))
    if "-slt" in diff and "+sltu" in diff or \
            "-sltu" in diff and "+slt" in diff:
        matches.append(_from_pattern(
            CATALOG["signed-comparison-type-family"],
            "the paired residual changes slt and sltu"))
    isolated = CATALOG["isolated-register-web-source-shape"]
    if (_isolated_register_web(packet) and
            (include_hypotheses or not isolated.is_hypothesis)):
        matches.append(_from_pattern(
            isolated,
            "equal instruction/text lengths and every classified fault is "
            "register allocation"))

    # Conservative target-only detectors already attached to confirmed
    # catalog patterns.  They are useful before a candidate exists too.
    seen = {match.pattern_id for match in matches}
    for pattern in CATALOG.values():
        if pattern.id in seen or pattern.detector is None:
            continue
        if pattern.is_hypothesis and not include_hypotheses:
            continue
        try:
            activated = bool(pattern.detector(target_asm))
        except Exception:
            activated = False
        if activated:
            matches.append(_from_pattern(
                pattern, "the catalog's target-assembly detector fired"))

    matches.sort(key=lambda match: (
        match.status != "CONFIRMED", match.pattern_id))
    return tuple(matches[:max(0, limit)])


def render(matches: tuple[Match, ...]) -> tuple[str, ...]:
    return tuple(match.render() for match in matches)

"""Retrieve compiler principles from a target/candidate residual.

Confirmed catalog entries are admissible solver knowledge.  Experimental
hypotheses are separately labelled and may be enabled only by an experiment;
they never silently become production rules.
"""

from __future__ import annotations

import re
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


_LOAD = re.compile(r"^(l[bhw]u?)\s+\$?\w+,\s*(-?(?:0x[0-9a-fA-F]+|\d+))\(\$?(\w+)\)$")
_STORE = re.compile(r"^(s[bhw])\s+\$?\w+,\s*(-?(?:0x[0-9a-fA-F]+|\d+))\(\$?(\w+)\)$")


_SAVED = re.compile(r"^l[bhw]u?\s+\$?(?:ra|s[0-8]|fp),")       # prologue/epilogue traffic, not operand order


def _accesses(stream: list[str], rx: re.Pattern) -> list[tuple[str, int, str]]:
    out = []
    for text in stream:
        m = rx.match(text)
        if m and not _SAVED.match(text):
            out.append((m.group(1), int(m.group(2), 0), m.group(3)))
    return out


def _extra_stack_slot(target: list[str], candidate: list[str]) -> str | None:
    """A non-argument register the candidate stores to a stack slot it later reloads, a slot the target never uses."""
    used = {m.group(1) for t in target if (m := re.search(r",\s*(-?\w+)\(\$?sp\)$", t))}
    for i, text in enumerate(candidate):
        m = re.match(r"^sw\s+\$?([tv]\d),\s*(-?\w+)\(\$?sp\)$", text)
        if m and m.group(2) not in used and any(re.match(rf"^lw\s+\$?\w+,\s*{m.group(2)}\(\$?sp\)$", x)
                                                for x in candidate[i + 1:]):
            return f"{m.group(1)} -> {m.group(2)}(sp)"
        if m and m.group(2) not in used and re.match(r"^(b|j)\b", candidate[i + 1] if i + 1 < len(candidate) else ""):
            return f"{m.group(1)} -> {m.group(2)}(sp) before a branch to the return"
    return None


def _swapped_pairs(target: list[tuple], candidate: list[tuple]) -> list[tuple]:
    """Adjacent access pairs (a, b) in the target that appear as (b, a) adjacent in the candidate."""
    cand = set(zip(candidate, candidate[1:]))
    return [(a, b) for a, b in zip(target, target[1:]) if a != b and (b, a) in cand and (a, b) not in cand]


def residual_rules(diff: str) -> list[tuple[str, str]]:
    """(catalog id, reason) for the 2026-10-02 rules a residual states on its face (patterns/catalog.py).

    Only signatures the diff itself shows are detected. `ido53-empty-arm-layout-conditions` has none: its branch
    signature is shared with other causes, so it is never claimed from a diff.
    """
    from solver import diffrepair, missing_store, site_edits
    target, candidate = diffrepair._streams(diff or "")
    out: list[tuple[str, str]] = []
    if missing_store.missing(diff):
        stores = ", ".join(f"{s['op']} 0x{s['offset']:X}" for s in missing_store.missing(diff))
        out.append(("target-only-store-is-a-missing-statement",
                    f"the target performs store(s) the candidate never does: {stores}"))
    hint = site_edits.widening_hint(diff)
    if hint:
        out.append(("candidate-only-extension-widens-a-declaration",
                    f"only the candidate re-extends a {'/'.join(sorted(hint))} value"))
    narrow = site_edits.widening_hint(_reversed(diff))
    if narrow:
        out.append(("s16-sign-extend",
                    f"only the target extends a {'/'.join(sorted(narrow))} value: the candidate declares it wider"))
    from solver import alignment
    for step in alignment.align_diff(diff or "").steps:
        t, c = step.target, step.candidate
        if step.ambiguous or t is None or c is None:
            continue
        tm, cm = _LOAD.match(t.text), _LOAD.match(c.text)
        if tm and cm and tm.group(1) != cm.group(1) and tm.group(2) == cm.group(2) and not _SAVED.match(t.text):
            out.append(("load-opcode-names-the-access-type",
                        f"the target loads offset {tm.group(2)} with {tm.group(1)}, the candidate with {cm.group(1)}"))
            break
    t_stores = [s for s in _accesses(target, _STORE) if s[2] != "sp"]
    c_stores = [s for s in _accesses(candidate, _STORE) if s[2] != "sp"]
    if t_stores != c_stores and sorted(t_stores) == sorted(c_stores):
        out.append(("ido53-adjacent-store-order-is-preserved",
                    "the same stores occur in a different order"))
    swapped = _swapped_pairs(_accesses(target, _LOAD), _accesses(candidate, _LOAD))
    if any(a[2] != "sp" for a, _b in swapped):
        out.append(("ido53-commutative-operand-materialisation-order",
                    "two loads from the same object are emitted in swapped order"))
    if any(a[2] == "sp" for a, _b in swapped):
        out.append(("ido53-o1-mirrored-comparison-keeps-source-order",
                    "two stack-home reloads are emitted in swapped order (-O1 operand order)"))
    slot = _extra_stack_slot(target, candidate)
    if slot:
        out.append(("ido53-o1-result-temporary-has-a-stack-home",
                    f"the candidate spills a value to a stack slot the target never uses ({slot})"))
    return out


def _reversed(diff: str) -> str:
    """The same diff with target and candidate exchanged (`-` <-> `+`), to read target-only rows as candidate-only."""
    out = []
    for row in (diff or "").splitlines():
        if row.startswith(("---", "+++")):
            out.append(row)
        elif row[:1] == "-":
            out.append("+" + row[1:])
        elif row[:1] == "+":
            out.append("-" + row[1:])
        else:
            out.append(row)
    return "\n".join(out)


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
    for pattern_id, reason in residual_rules(diff):
        matches.append(_from_pattern(CATALOG[pattern_id], reason))
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

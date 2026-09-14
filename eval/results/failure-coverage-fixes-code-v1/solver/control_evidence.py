"""Concrete load-interpretation contrasts upstream of bad observables.

Pairing by address/width/occurrence is diagnostic, never an equivalence proof.
This catches control dependencies absent from the bad value's arithmetic DAG.
"""
from collections import defaultdict
import json
import re


LOAD = re.compile(r"^load (.+)/(1|2|4)=(0x[0-9a-f]+);")
SIGNEDNESS_PAIRS = {frozenset(("lb", "lbu")), frozenset(("lh", "lhu"))}


def _reads(run, before):
    grouped = defaultdict(list)
    for event in run.trace[:before]:
        match = LOAD.match(event.effect)
        if match and not match.group(1).startswith("stack"):
            grouped[(match.group(1), int(match.group(2)))].append(
                (event, int(match.group(3), 16)))
    return grouped


def _dependent_branches(run, load, before):
    marker = f"i{load.instruction} "
    return [{"instruction": event.text, "effect": event.effect,
             "operands": [{"register": reg, "value": hex(value), "origin": origin}
                          for reg, value, origin in event.reads]}
            for event in run.trace[load.ordinal + 1:before]
            if event.effect.startswith("branch ") and
            any(marker in origin for _, _, origin in event.reads)][:2]


def contrasts(results, limit=6):
    grouped = {}
    for result in results:
        if result.status != "failed":
            continue
        target_end, candidate_end = len(result.target.trace), len(result.candidate.trace)
        for target, candidate in zip(result.target.writes, result.candidate.writes):
            if (target.address, target.width, target.value) != (candidate.address, candidate.width, candidate.value):
                target_end, candidate_end = target.trace_position, candidate.trace_position
                break
        left = _reads(result.target, target_end)
        right = _reads(result.candidate, candidate_end)
        seen_in_case = set()
        for (address, width), target_reads in left.items():
            for (target, a), (candidate, b) in zip(target_reads, right.get((address, width), [])):
                target_op, candidate_op = target.text.split()[0], candidate.text.split()[0]
                mask = (1 << (width * 8)) - 1
                if a == b or (a & mask) != (b & mask) or frozenset((target_op, candidate_op)) not in SIGNEDNESS_PAIRS:
                    continue
                key = (address, width, target_op, candidate_op)
                if key not in grouped:
                    if len(grouped) >= limit:
                        continue
                    grouped[key] = {
                        "address": address, "width": width,
                        "raw_memory_bits": hex(a & mask),
                        "target": {"instruction": target.text, "loaded_value": hex(a),
                                   "dependent_branches": _dependent_branches(result.target, target, target_end)},
                        "candidate": {"instruction": candidate.text, "loaded_value": hex(b),
                                      "dependent_branches": _dependent_branches(result.candidate, candidate, candidate_end)},
                        "example_case": result.case, "support_cases": 0}
                if key not in seen_in_case:
                    grouped[key]["support_cases"] += 1
                    seen_in_case.add(key)
    return list(grouped.values())


def render(results):
    evidence = contrasts(results)
    if not evidence:
        return ""
    return ("UPSTREAM SAME-ADDRESS LOAD CONTRASTS (before the bad observable):\n"
            "The same raw bits were loaded with different sign extension. lb/lh sign-extend; "
            "lbu/lhu zero-extend. This is a concrete type/cast difference, not evidence of a bad "
            "symbol relocation. A changed predicate can select different valid pointers for a later "
            "store; inspect the comparison and reaching definition before changing an address. "
            "Address/occurrence alignment is diagnostic only; whole-panel rerun remains mandatory.\n" +
            json.dumps(evidence, sort_keys=True))


def source_variants(results, source, limit=4):
    """Enumerate narrow cast experiments, not assumed-correct substitutions."""
    from solver import c89
    from solver.principle_variants import Variant
    masked = c89._mask(source)
    seen, variants = set(), []
    for row in contrasts(results):
        target = row["target"]["instruction"].split()[0]
        width = row["width"]
        to_unsigned = target in {"lbu", "lhu"}
        if width == 2:
            old = r"(?:s16|signed\s+short|short)" if to_unsigned else r"(?:u16|unsigned\s+short)"
            new = "unsigned short" if to_unsigned else "signed short"
        else:
            old = r"(?:s8|signed\s+char)" if to_unsigned else r"(?:u8|unsigned\s+char)"
            new = "unsigned char" if to_unsigned else "signed char"
        for match in re.finditer(r"\(\s*" + old + r"\s*\)", masked):
            replacement = source[:match.start()] + "(" + new + ")" + source[match.end():]
            if replacement in seen:
                continue
            seen.add(replacement)
            variants.append(Variant(f"observed-load-extension:{width}:{match.start()}:{new}", replacement))
            if len(variants) >= limit:
                return tuple(variants)
    return tuple(variants)

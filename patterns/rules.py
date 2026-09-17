"""The rules a derivation run can evaluate.

Each rule is a claim about compiler behaviour with a `predict` that produces source it believes the
compiler will accept as exact. `patterns/derive.py` runs them and refuses to confirm one that only
closes the case it came from.

The registry is deliberately small and explicit. A rule enters it with a `derivation_case`, and stays
`Unconfirmed` until the harness closes something else with it.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass
from typing import Sequence

from patterns import ordering
from patterns.derive import Case, Variant

# An assignment to a struct member through a pointer: `arg0->field = value;` and its compound forms.
_ASSIGN = re.compile(r"^\s*(?P<target>\w+\s*->\s*\w+)\s*(?P<op>=|\+=|-=|\|=|&=)\s*.+;\s*$")
# A store mnemonic in the normalized object dump.
_STORE = re.compile(r"^\s*(sw|sh|sb|swc1|sdc1)\b")


def store_statements(source: str) -> tuple[int, list[str]]:
    """The longest run of consecutive `ptr->member <op>= ...;` statements.

    Returns (first line index, lines). A run rather than every assignment in the function, because the
    permutation only makes sense over statements the compiler is free to reorder -- two assignments
    with a call between them are not a group.
    """
    lines = source.splitlines(keepends=True)
    best_start = -1
    best: list[str] = []
    start, run = -1, []
    for index, line in enumerate(lines):
        if _ASSIGN.match(line):
            if start < 0:
                start, run = index, []
            run.append(line)
        else:
            if len(run) > len(best):
                best_start, best = start, run
            start, run = -1, []
    if len(run) > len(best):
        best_start, best = start, run
    return best_start, best


def stores_in(sequence: Sequence[str]) -> list[str]:
    return [line for line in sequence if _STORE.match(line)]


@dataclass
class StoreOrderRule:
    """Reorder independent store statements into the target's emission order.

    Derived from Fstop: the candidate emitted five `NULL` stores in C statement order, the target
    emitted the same five in a different order, and rewriting the statements to the target's order
    closed it exactly (99.999 -> 100.0).

    SCOPE, and the harness is what found it. The first held-out case had a LOAD moved instead of a
    store, and the analogous change took it from 99.712 to 97.115 with regalloc faults 0 -> 36.
    `applies` therefore refuses any residual whose moved instructions are not all stores, which is a
    claim the rule makes about itself rather than something the caller has to remember.
    """
    id: str = "ordering-store-reorder"
    derivation_case: str | None = "Fstop"

    def applies(self, case: Case) -> bool:
        cause = ordering.classify(case.diff)
        if cause.name != "order":
            return False
        moved = ordering.moved_instructions(case.diff)
        return bool(moved) and all(_STORE.match(m) for m in moved)

    def predict(self, case: Case) -> Sequence[Variant]:
        start, statements = store_statements(case.source)
        if start < 0 or len(statements) < 2:
            return ()
        # The permuted group is the stores sharing one base. Fstop's hunk also carries the function's
        # argument spill `sw a1,4(sp)`, which is a store in the same hunk and is NOT part of the
        # permutation; counting it made the arity 6 against 5 statements and the rule declined its
        # own derivation case.
        moved = ordering.moved_instructions(case.diff)
        bases = {ordering.store_base(m) for m in moved}
        if len(bases) != 1 or None in bases:
            return ()
        candidate_seq, target_seq = ordering.hunk_store_orders(case.diff, bases=bases)
        if candidate_seq is None or len(candidate_seq) != len(statements):
            return ()
        # Map each target store to the statement that emitted its candidate counterpart. Stores are
        # matched by full instruction text, so an offset touched twice is ambiguous and declines
        # rather than guessing -- the same discipline align_positional uses for fields.
        counts = collections.Counter(candidate_seq)
        if any(n > 1 for n in counts.values()):
            return ()
        index_of = {text: i for i, text in enumerate(candidate_seq)}
        if sorted(counts) != sorted(collections.Counter(target_seq)):
            return ()
        order = [index_of[text] for text in target_seq]
        if sorted(order) != list(range(len(statements))):
            return ()
        if order == list(range(len(statements))):
            return ()
        lines = case.source.splitlines(keepends=True)
        reordered = [statements[i] for i in order]
        out = lines[:start] + reordered + lines[start + len(statements):]
        return (Variant(label="store-reorder", source="".join(out)),)


@dataclass
class TargetLinkageRule:
    """A model-written `static inline` target function does not build; IDO rejects C99 `inline` and
    discards an unreferenced `static`.

    Derived from two failures that were booked as separate problems (kb-sbk1.sqlite receipts 31124 and
    31125): `static inline void *f(void)` is a `cfe: Syntax Error` at the opening brace, and
    `static void *f(void)` -- what you get after deleting `inline` alone -- compiles to an object with
    NO TEXT SYMBOLS, because IDO does not emit a `static` function nothing calls. One bug, two
    symptoms: the admission write-up counted the first as "syntax" (63 functions) and the second as
    "no text symbols" (33).

    The fix is deterministic and needs no model: `c89.to_c89` deletes `inline`, and
    `c89.public_definition` drops `static` from the TARGET's own definition line while leaving
    helpers, tables and file-scope data alone.

    AN ADMISSION RULE, hence `criterion = "compiled"`. It makes functions build; it does not make them
    match. Confirmed on eleven held-out failures in the never-compiled re-run -- 11 of the 39
    functions that went from never-compiling to compiling did so only because of this rewrite, none of
    which was the case it was derived from.
    """
    id: str = "target-linkage-static-inline"
    derivation_case: str | None = "addRacePlayerScore"
    criterion: str = "compiled"
    case_kind: str = "admission"

    def applies(self, case: Case) -> bool:
        line = definition_line(case.source, case.function)
        return line is not None and ("inline" in line or "static" in line)

    def predict(self, case: Case) -> Sequence[Variant]:
        from solver import c89
        fixed = c89.public_definition(c89.to_c89(case.source), case.function)
        if fixed == case.source:
            return ()
        return (Variant(label="c89+public-definition", source=fixed),)


def definition_line(source: str, function: str) -> str | None:
    """The line that DEFINES `function`, or None when the source does not define it.

    Name-anchored and line-scoped, matching `c89.public_definition`: a `static` helper with a
    different name must not make this rule fire. A definition line does not end in `;` -- that is a
    declaration, and a declaration's `static` is not ours to remove.
    """
    if not function:
        return None
    name = re.compile(r"\b%s\s*\(" % re.escape(function))
    for line in source.splitlines():
        stripped = line.split("//")[0].strip()
        if name.search(line) and not stripped.endswith(";"):
            return line
    return None


RULES: dict[str, object] = {}


def register(rule) -> object:
    RULES[rule.id] = rule
    return rule


register(StoreOrderRule())
register(TargetLinkageRule())

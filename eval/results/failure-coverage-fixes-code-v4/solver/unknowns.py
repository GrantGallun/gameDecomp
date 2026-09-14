"""Enumerate what a function's source does not yet pin. See LEDGER.md.

`eval/triage.py` and `solver/signals.py` classify residuals AFTER a compile:
they need a diff. So a draft that does not compile scores 0, and 1,823
never-attempted functions plus ~140 non-compiling drafts are all 0 and mutually
indistinguishable. Unknowns are enumerable BEFORE a compile, from the target
assembly and the draft alone, which is the only way to rank that population.

An Unknown is a QUESTION, not a claim. Its `status` says whether an answer is
available; `resolver` says which pass could produce one. That inverts the
`inference` tier's original design -- it was built to store answers and holds 0
rows -- without changing its schema.

The capability sketch follows BinSub (arXiv 2409.01841) and Retypd (PLDI 2016):
what a value can DO, not what it is called. Record fields are (offset, size)
pairs with separate load and store polarity, because one polarity alone loses
information. Our evidence rows already carry offset, width, signedness and
is_load, so the binary half needs no new extraction.

Nothing here reads include/game/** or any target .c file. symbol_addrs.txt is
joined on ADDRESS only; its `// size:0x4` annotations are the decomp team's
curation and are deliberately not parsed.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from solver import structgen, typedecl

# kind -> (resolver module or None, state). State drives ranking weight:
# a kind nothing can discharge is worth more than a dozen that something can.
RESOLVERS: dict[str, tuple[str | None, str]] = {
    "undeclared_type":     ("solver.typedecl", "wired"),
    "struct_layout":       ("solver.structgen", "wired"),
    "loop_form":           ("solver.rewrites", "wired"),
    "undeclared_global":   ("miner.globals_layout", "unwired"),
    "callee_signature":    ("solver.protostore", "partial"),
    "absent_body":         (None, "none"),
    "branch_shape":        (None, "none"),
    "jump_table":          (None, "none"),
    "register_allocation": (None, "none"),
    "field_name":          (None, "unpinnable"),
    "field_extent":        (None, "unpinnable"),
}

STATE_WEIGHT = {"none": 100, "unwired": 10, "partial": 5,
                "wired": 1, "unpinnable": 0}

# name = 0xADDRESS. The trailing `// size:` comment is NOT captured.
SYMBOL_LINE = re.compile(r"^\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)", re.M)
IDENTIFIER = re.compile(r"\b[A-Za-z_]\w*\b")
DEREFERENCED = re.compile(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)\s*->")
SUBSCRIPTED_MEMBER = re.compile(r"\b([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)\s*\[")
SUBSCRIPTED_PLAIN = re.compile(r"\b([A-Za-z_]\w*)\s*\[")
CALLED = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
DO_BLOCK = re.compile(r"\bdo\s*\{")


@dataclass(frozen=True)
class Unknown:
    subject: str
    kind: str
    status: str                       # known | constrained | free | unpinnable
    capabilities: dict = field(default_factory=dict)
    resolver: str | None = None
    cites: tuple[int, ...] = ()

    @property
    def weight(self) -> int:
        return STATE_WEIGHT[RESOLVERS[self.kind][1]]


def symbol_table(repo: Path) -> dict[str, int]:
    """name -> address, from symbol_addrs.txt. Addresses only, never sizes."""
    path = Path(repo).expanduser() / "symbol_addrs.txt"
    if not path.is_file():
        return {}
    return {m.group(1): int(m.group(2), 16)
            for m in SYMBOL_LINE.finditer(path.read_text(errors="replace"))}


def _global_subject(addr: int) -> str:
    """Subjects must be spelled the way the evidence tier spells bases.

    `global:0x801107D8`, not `0x801107d8`. Fixing only the query left subjects
    in the other case, so a ledger row could not be joined back to the rows it
    cites -- caught by tests/test_grounded.py, not by the unit tests.
    """
    return f"global:0x{addr:08X}"


def _global_evidence(conn, addr: int) -> tuple[list[dict], bool, bool,
                                               tuple[int, ...]]:
    """Accessed slots, load/store polarity and citations for one address.

    Program-wide by construction: a global's layout is not a per-function fact.
    That is globals_layout's finding -- a function sees 62 field offsets on the
    globals it touches while the rest of the program sees 166.
    """
    # The evidence tier spells these `global:0x801107D8` -- lowercase `0x`
    # prefix, UPPERCASE hex digits. Formatting with `{addr:#010x}` matched only
    # the 133 of 1,166 addresses that happen to contain no hex letters, and
    # returned zero citations for the other 1,033 without erroring. Caught on
    # the enumerator's first live run by LEDGER.md's own gate, which is the
    # entire reason that gate is written down. Both spellings are queried.
    # Deduped: an address with no hex letters spells the same both ways, and
    # passing it twice relied on SQL's IN semantics to collapse the duplicate.
    # Real SQLite does; a test double concatenating its params does not, so the
    # two disagreed. Make the dedup explicit rather than implicit.
    spellings = sorted({f"global:0x{addr:08X}", f"global:0x{addr:08x}"})
    marks = ",".join("?" * len(spellings))
    rows = conn.execute(
        "SELECT id, offset, width, signed, is_load FROM evidence"
        f" WHERE kind = 'mem_access' AND base IN ({marks})",
        tuple(spellings)).fetchall()
    slots: dict[int, dict] = {}
    loaded = stored = False
    cites: list[int] = []
    for row in rows:
        row_id, off, width, signed, is_load = row
        cites.append(row_id)
        loaded = loaded or bool(is_load)
        stored = stored or not is_load
        prev = slots.get(off)
        if prev is None or (width or 0) > prev["width"]:
            slots[off] = {"offset": off, "width": width, "signed": signed}
    return ([slots[o] for o in sorted(slots)], loaded, stored, tuple(cites))


def draft_capabilities(code: str, variable: str) -> dict:
    """What the DRAFT does through `variable` -- the source half of the sketch.

    `dereferenced` and `subscripted` are why typedecl only rescued 4 of 25
    plans on 2026-09-01: it declared every member a scalar, and 7 of the 21
    failures were `Subscripting a non-array` or `Selector requires
    struct/union pointer`. Those are capabilities, not special cases.
    """
    deref = [m.group(2) for m in DEREFERENCED.finditer(code)
             if m.group(1) == variable]
    subs = [m.group(2) for m in SUBSCRIPTED_MEMBER.finditer(code)
            if m.group(1) == variable]
    return {
        "dereferenced": sorted(set(deref)),
        "subscripted": sorted(set(subs)),
        "members": typedecl.members_used(code, {variable}),
    }


def declared_names(code: str) -> set[str]:
    """Identifiers the draft itself introduces: params, locals, its own types."""
    names = set()
    for m in re.finditer(r"\b(?:[A-Za-z_]\w*\s+)+\**([A-Za-z_]\w*)\s*(?=[;,)=\[])",
                         code):
        names.add(m.group(1))
    for m in re.finditer(r"\}\s*\**\s*([A-Za-z_]\w*)\s*;", code):
        names.add(m.group(1))
    return names


def enumerate_unknowns(conn, func: str, code: str, known_types: set[str],
                       symbols: dict[str, int]) -> list[Unknown]:
    """Every question this draft leaves open. Pure: no compile, no workspace."""
    out: list[Unknown] = []
    layout = structgen.layout(conn, func)
    local = declared_names(code) | {func}

    # An ABSENT body is the maximal unknown, not the minimal one. m2c writes
    # `// file is blank because m2c failed to decompile function` and nothing
    # else; the draft then has no parameters, no members and no globals to
    # enumerate, so the first version of this scored those functions zero and
    # ranked them as the EASIEST work available. 26 of the 42 zero-weight
    # leaves were this. Caught by LEDGER.md's gate 2, which is the whole point
    # of writing the gate down before the enumerator.
    if typedecl.definition_params(code, func) is None:
        return [Unknown(f"{func}:body", "absent_body", "free",
                        {"m2c_declined": True})]

    # --- parameters: undeclared type + the layout behind it -----------------
    for index, type_name, var in typedecl.pointer_parameters(code, func):
        caps = draft_capabilities(code, var)
        fields = layout.get(f"param{index}") or []
        caps["accessed"] = [{"offset": o, "width": w, "signed": None}
                            for o, w, _t in fields]
        if not (type_name in known_types or typedecl.declared_in(code, type_name)):
            out.append(Unknown(f"{func}:param{index}", "undeclared_type",
                               "free", caps, RESOLVERS["undeclared_type"][0]))
        if fields:
            out.append(Unknown(f"{func}:param{index}", "struct_layout",
                               "free", caps, RESOLVERS["struct_layout"][0]))
        # Named members are hypotheses forever; extents of subscripted members
        # cannot be read off the binary at all. Recorded so the ledger does not
        # accumulate questions that have no possible answer.
        for member in caps["members"]:
            out.append(Unknown(f"{func}:param{index}.{member}", "field_name",
                               "unpinnable"))
        for member in caps["subscripted"]:
            out.append(Unknown(f"{func}:param{index}.{member}", "field_extent",
                               "unpinnable"))

    # --- globals the draft names but nothing declares -----------------------
    used = set(IDENTIFIER.findall(code)) - local - known_types
    called = {m.group(1) for m in CALLED.finditer(code)}
    plain_subscripted = {m.group(1) for m in SUBSCRIPTED_PLAIN.finditer(code)}
    for name in sorted(used):
        addr = symbols.get(name)
        if addr is None or name in called:
            continue
        slots, loaded, stored, cites = _global_evidence(conn, addr)
        caps = {"accessed": slots, "loaded": loaded, "stored": stored,
                "subscripted": [name] if name in plain_subscripted else []}
        out.append(Unknown(_global_subject(addr), "undeclared_global",
                           "free", caps,
                           RESOLVERS["undeclared_global"][0], cites))
        if name in plain_subscripted:
            out.append(Unknown(_global_subject(addr), "field_extent",
                               "unpinnable"))

    # --- callees with no verified signature ---------------------------------
    for name in sorted(called - local - known_types):
        if name in symbols or re.match(r"^(?:if|for|while|switch|return|sizeof)$",
                                       name):
            continue
        out.append(Unknown(f"callee:{name}", "callee_signature", "free",
                           {"called": True}, RESOLVERS["callee_signature"][0]))

    # --- control flow the build forbids spelling directly -------------------
    if DO_BLOCK.search(code):
        out.append(Unknown(f"{func}:body", "loop_form", "free",
                           {"do_token": True}, RESOLVERS["loop_form"][0]))
    return out


def free_weight(unknowns: list[Unknown]) -> int:
    """Ranking key: ascending. Unpinnable rows contribute nothing."""
    return sum(u.weight for u in unknowns if u.status == "free")


def persist(conn, unknowns: list[Unknown], origin: str) -> int:
    """Write to the existing inference tier. No new table, no migration.

    CLAUDE.md rule 4: every inference cites evidence. Enforced here rather than
    trusted -- a non-free row with no support is refused, not logged.
    """
    written = 0
    now = int(time.time())
    for u in unknowns:
        if u.status not in ("free", "unpinnable") and not u.cites:
            raise ValueError(f"uncited {u.status} claim: {u.subject}/{u.kind}")
        cur = conn.execute(
            "INSERT INTO inference (kind, subject, value, confidence, origin,"
            " status, created_at) VALUES (?,?,?,?,?,?,?)",
            (u.kind, u.subject, json.dumps(u.capabilities, sort_keys=True),
             None, origin, u.status, now))
        for evidence_id in u.cites:
            conn.execute("INSERT INTO inference_support VALUES (?,?)",
                         (cur.lastrowid, evidence_id))
        written += 1
    conn.commit()
    return written

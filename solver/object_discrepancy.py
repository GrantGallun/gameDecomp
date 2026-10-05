"""Structured discrepancies between a target and a candidate object image.

The normalized asm diff is lossy (trailing nops, `jtbl_*` -> `.rodata`, relocation spellings), so a
candidate can read as exact there while its object differs. On kb-sbk1 that is 96 attempts across 24
functions (measured 2026-09-30). This module reads the same `byte_certificate.object_image` records
the certificate does and says WHAT differs, as data, so nothing needs to be recovered from text.

Every row carries a `lever`: what, if anything, resolves it. `unknown` is the default and is a
finding to explain, not a null to accept. A lever is assigned only for a pattern that has been
confirmed on real cases (`patterns/catalog.py`); a hypothesis does not get to set one. No lever is
confirmed yet, so every row is `unknown`; see tests/test_object_discrepancy.py.

Pure functions of the two images, so they also run on the receipts already logged in `attempts`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

UNKNOWN = "unknown"

_SCALAR_FIELDS = ("alignment", "flags", "type")


@dataclass(frozen=True)
class Discrepancy:
    kind: str                   # closed set, see KINDS
    section: str
    offset: int | None          # byte offset in the section when the row has one
    target: object
    candidate: object
    detail: str = ""            # sub-classification, observed fact only
    lever: str = UNKNOWN
    evidence: str = "byte_certificate.object_image"
    candidate_line: int | None = None   # C line, only for `.text` rows the compiler attributes

    def to_dict(self) -> dict:
        return asdict(self)


KINDS = {"section_missing", "section_extra", "section_size", "section_bytes", "section_attr",
         "reloc_order", "reloc_identity", "reloc_missing", "reloc_extra"}


def _reloc(row) -> tuple:
    at, kind, identity = row
    return (at, kind, tuple(identity))


def _identity_detail(a: tuple, b: tuple) -> str:
    if a[0] != b[0]:
        return f"{a[0]}_vs_{b[0]}"          # e.g. section_vs_external: `.rodata` against `D_800E128C`
    if a[0] == "external" and a[1] != b[1]:
        return "symbol_name"
    return "addend_or_binding"


def _relocations(name: str, left: list, right: list) -> list[Discrepancy]:
    a, b = [_reloc(r) for r in left], [_reloc(r) for r in right]
    if a == b:
        return []
    if Counter(a) == Counter(b):
        first = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y)
        return [Discrepancy("reloc_order", name, a[first][0], a[first], b[first],
                            detail=f"same {len(a)} relocations, different order")]
    out = []
    only_a = Counter(a) - Counter(b)
    only_b = Counter(b) - Counter(a)
    by_at_b: dict = {}
    for row in sorted(only_b.elements()):
        by_at_b.setdefault((row[0], row[1]), []).append(row)
    for row in sorted(only_a.elements()):
        partner = (by_at_b.get((row[0], row[1])) or [None]).pop(0)
        if partner is None:
            out.append(Discrepancy("reloc_missing", name, row[0], row, None))
        else:
            out.append(Discrepancy("reloc_identity", name, row[0], row, partner,
                                   detail=_identity_detail(row[2], partner[2])))
    for rows in by_at_b.values():
        for row in rows:
            out.append(Discrepancy("reloc_extra", name, row[0], None, row))
    return out


def compare(target_image: dict, candidate_image: dict) -> list[Discrepancy]:
    """Discrepancies between two `object_image` records; empty means identical images."""
    left, right = target_image["sections"], candidate_image["sections"]
    out: list[Discrepancy] = []
    for name in sorted(set(left) | set(right)):
        if name not in right:
            out.append(Discrepancy("section_missing", name, None, left[name]["size"], None))
            continue
        if name not in left:
            out.append(Discrepancy("section_extra", name, None, None, right[name]["size"]))
            continue
        a, b = left[name], right[name]
        if a["size"] != b["size"]:
            delta = a["size"] - b["size"]
            out.append(Discrepancy(
                "section_size", name, None, a["size"], b["size"],
                detail=f"target longer by {delta}" if delta > 0 else f"candidate longer by {-delta}"))
        elif a["sha256"] != b["sha256"]:
            out.append(Discrepancy("section_bytes", name, None, a["sha256"], b["sha256"]))
        for field in _SCALAR_FIELDS:
            if a.get(field) != b.get(field):
                out.append(Discrepancy("section_attr", name, None, a.get(field), b.get(field), detail=field))
        out += _relocations(name, a["relocations"], b["relocations"])
    return out


def attach_c_lines(rows: list[Discrepancy], attribution: dict | None) -> list[Discrepancy]:
    """Fill `candidate_line` on `.text` rows whose offset the compiler's line records map."""
    from solver import source_attribution
    if not attribution or attribution.get("status") != "verified":
        return rows
    by_address = {r["address"]: r["candidate_line"] for r in source_attribution.instructions_of(attribution)
                  if r.get("section") == ".text" and r.get("candidate_line") is not None}
    out = []
    for row in rows:
        line = by_address.get(row.offset) if row.section == ".text" and row.offset is not None else None
        out.append(row if line is None else Discrepancy(**{**asdict(row), "candidate_line": line}))
    return out


def from_verification(verification: dict | None) -> list[Discrepancy]:
    """Discrepancies for a logged certificate receipt (needs its two images)."""
    if not verification or "target_image" not in verification or "candidate_image" not in verification:
        return []
    return compare(verification["target_image"], verification["candidate_image"])


def hidden_from_asm(verification: dict | None) -> bool:
    """The normalized asm reported exact but the byte certificate did not."""
    return bool(verification and verification.get("normalized_assembly_exact") and not verification.get("exact"))


# ---------------------------------------------------------------------------------------------------------------------
# Routing. The rows above are observations; which ones a C edit can fix is inference, so each rule below carries the
# measurement it rests on. Census 2026-09-30 (eval/results/hidden-object-20260930): 155 of 828 unsolved best candidates
# differ outside .text, none by a wrong constant; with .text byte-identical, every row was one of these.

DATA = {".rodata", ".late_rodata", ".data", ".bss", ".sdata", ".sbss"}

# Rows that, with .text byte-identical, a C edit cannot improve: the certificate or integration decides them.
EXPLAINED = {
    ("reloc_order", ".text"): "assembler relocation record order (eval/results/reloc-pairing-20260924)",
    ("section_size", ".text"): "trailing zero padding only (summarize checks the bytes); function_boundary owns it",
    # reloc_identity rows are explained only for these details (see _explained); `symbol_name` is a different symbol
    # at the same site -- a C fault (drawCharacterSelectCourseExitPreviewPanel), not a spelling.
    ("reloc_identity", ".text"): "same address spelled as section+addend vs symbol; the certificates resolve both",
    ("section_attr", ".rodata"): "IDO aligns .rodata to 16; the target, assembled from split asm, to 4",
    ("section_size", ".rodata"): "IDO pads .rodata to its 16-byte alignment",
    ("section_extra", ".rodata"): "candidate owns a jump table or literal the target keeps under a label",
    ("section_missing", ".rodata"): "candidate reads the target's label externally",
}
# Rows a confirmed generator acts on (patterns/catalog.py). A lever only exists here once confirmed.
LEVERS = {
    "rodata_address": "solver.rodata_symbol.address_variants",
    "unused_file_scope_object": "solver.file_scope_objects.variants",
}


SPELLINGS = {"addend_or_binding", "section_vs_external", "external_vs_section"}


def _explained(row: Discrepancy) -> bool:
    if row.kind == "reloc_identity" and row.detail not in SPELLINGS:
        return False
    return (row.kind, row.section) in EXPLAINED


def classify(rows: list[Discrepancy], *, text_identical: bool, address_sites: int = 0,
             unused_objects: int = 0) -> dict:
    """Route for one compiled candidate: exact | c_edit | generator | certify | unexplained.

    - exact: no rows.
    - c_edit: .text differs; ordinary repair owns it. Hidden rows ride along as `hidden` so they are not lost.
    - generator: a confirmed lever applies (listed in `levers`), whatever .text says.
    - certify: .text identical and every row EXPLAINED -- no C edit can help; spend no model calls on it.
    - unexplained: .text identical and a row no rule covers. A finding to trace, never a silent null.
    """
    hidden = [r for r in rows if r.section in DATA]
    levers = []
    if address_sites:
        levers.append("rodata_address")
    if unused_objects and any(r.section in {".bss", ".data"} and r.kind == "section_extra" for r in rows):
        levers.append("unused_file_scope_object")
    unexplained = [r for r in rows if not _explained(r)
                   and not (r.section in {".bss", ".data"} and "unused_file_scope_object" in levers)
                   and not (r.detail == "symbol_name" and "rodata_address" in levers)]
    if not rows:
        route = "exact"
    elif levers:
        route = "generator"
    elif not text_identical:
        route = "c_edit"
    elif unexplained:
        route = "unexplained"
    else:
        route = "certify"
    return {"route": route, "text_identical": text_identical, "levers": levers,
            "rows": [{"kind": r.kind, "section": r.section, "offset": r.offset, "detail": r.detail} for r in rows],
            "hidden": len(hidden),
            "unexplained": [f"{r.kind}:{r.section}" for r in unexplained] if text_identical else []}


def same_text(target: bytes | None, candidate: bytes | None) -> bool:
    """Equal .text, or equal up to a run of trailing zero bytes on one side (section alignment padding: the 16-byte
    group of the hidden-from-asm set, which function_boundary certifies on the function's own extent)."""
    if target is None or candidate is None:
        return False
    short, long_ = sorted((target, candidate), key=len)
    return long_[:len(short)] == short and not any(long_[len(short):])


def summarize(target_obj: bytes, candidate_obj: bytes, source: str | None = None, function: str = "") -> dict:
    """classify() from the two objects of one compile. With the candidate source, a lever counts only when its
    generator actually produces a rewrite; without it, only address sites the candidate does not already name."""
    from solver import byte_certificate as bc, rodata_symbol
    left, right = bc.object_image(target_obj), bc.object_image(candidate_obj)
    rows = compare(left, right)
    text_identical = same_text(bc.section_contents(target_obj).get(".text"),
                               bc.section_contents(candidate_obj).get(".text"))
    sites = unused = 0
    if rows:
        facts = rodata_symbol.address_facts(target_obj, candidate_obj)
        if source is not None:
            sites = int(rodata_symbol.address_rewrite(source, function, facts)[0] is not None)
        else:
            sites = sum(1 for s in facts.get("sites", ()) if s["candidate"] != ["external", s["target_label"]])
        if source is not None and any(r.section in {".bss", ".data"} for r in rows):
            from solver import file_scope_objects
            unused = len(file_scope_objects.unused(source))
    return classify(rows, text_identical=text_identical, address_sites=sites, unused_objects=unused)

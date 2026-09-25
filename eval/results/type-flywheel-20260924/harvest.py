"""Harvest declarations from the pipeline's own SOLVED exact sources (no reference source, no game headers).

SOLVED = eval.status tiers: exact in the KB, minus recovered, header-assisted and reference-type-assisted; further
restricted here to sources with no `#include "game/` (the documented gap in eval.status). From each such function's
exact source (the latest gate-passing one): its own definition's signature (verified by the match), extern global
declarations, function prototypes, and struct/typedef definitions those need. Bodies are never harvested.

    python3 harvest.py   -> E/harvest.json (declarations by symbol) and survey counts on stdout
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from eval import status  # noqa: E402
from tools import n64_corpus  # noqa: E402

E = Path.home() / "decomp/experiments/type-flywheel-20260924"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
EXTERN = re.compile(r"^[ \t]*extern[ \t]+(?!\"C\")([^;(]*?)\b([A-Za-z_]\w*)[ \t]*((?:\[[^\]]*\])*)[ \t]*;", re.M)
PROTO = re.compile(r"^[ \t]*(?:extern[ \t]+)?([A-Za-z_][\w \t\*]*?)[ \t\*]*\b([A-Za-z_]\w*)[ \t]*\(([^;{}()]*)\)[ \t]*;", re.M)
AGG = re.compile(r"^(typedef[ \t]+)?(struct|union)[ \t]+([A-Za-z_]\w*)?[ \t]*\{", re.M)


def solved_sources(conn) -> dict[str, str]:
    q = conn.execute
    recovered = {n for (n,) in q("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                 "where a.exact=1 and (a.strategy like '%history-recovery%' or a.strategy like "
                                 "'%historical-provenance%' or a.strategy like '%symbol-restoration%')")}
    header = {n for (n,) in q("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                              "where a.exact=1 and a.strategy like '%project-header%'")}
    ref_types = status.reference_only_types(REPO)
    ident = re.compile(r"\b[A-Z]\w*\b")
    out = {}
    for name, strategy, source, sampling in q(
            "select f.name, a.strategy, a.source_code, a.sampling from attempts a join functions f "
            "on f.addr=a.func_addr where a.exact=1 and a.source_code is not null order by a.id"):
        if name in recovered or name in header:
            continue
        if '#include "game/' in source:
            continue
        # gate: the latest recorded verdicts carry the frontend result; older exacts predate the gate
        try:
            fe = (json.loads(sampling) or {}).get("frontend") if sampling else None
        except ValueError:
            fe = None
        if fe is not None and fe.get("passed") is False:
            continue
        text = re.sub(r"//[^\n]*|/\*.*?\*/", " ", source, flags=re.S)
        if set(ident.findall(text)) & ref_types:
            continue
        out[name] = source
    return out


def aggregates(source: str) -> list[tuple[str, str]]:
    """(tag or typedef name, full definition text) for each struct/union definition at file scope."""
    masked = n64_corpus._mask_noncode(source)
    res = []
    for m in AGG.finditer(masked):
        close = n64_corpus._matching_right(masked, masked.index("{", m.start()), "{", "}")
        if close is None:
            continue
        end = masked.find(";", close)
        text = source[m.start():end + 1]
        name = m.group(3) or (re.search(r"\}\s*([A-Za-z_]\w*)\s*;$", text) or [None, None])[1]
        if name:
            res.append((name, text))
    return res


def main():
    conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    sources = solved_sources(conn)
    by_symbol = collections.defaultdict(lambda: collections.defaultdict(set))
    structs = collections.defaultdict(set)
    for fn, src in sources.items():
        masked = n64_corpus._mask_noncode(src)
        rec = next((r for r in n64_corpus.extract_functions(src) if r["name"] == fn), None)
        if rec:
            d = str(rec["definition"])
            sig = re.sub(r"\s+", " ", d[:d.index("{")]).strip()
            by_symbol[fn]["signature"].add(sig + ";")
        head = masked[:masked.find(str(rec["definition"])[:20])] if rec else masked
        for m in EXTERN.finditer(head):
            by_symbol[m.group(2)]["global"].add(re.sub(r"\s+", " ", m.group(0).strip()))
        for m in PROTO.finditer(head):
            if m.group(2) in ("if", "while", "for", "switch", "return", "sizeof"):
                continue
            by_symbol[m.group(2)]["prototype"].add(re.sub(r"\s+", " ", m.group(0).strip()))
        for name, text in aggregates(src):
            structs[name].add(text)
    E.mkdir(parents=True, exist_ok=True)
    out = {"solved_sources": sorted(sources),
           "symbols": {s: {k: sorted(v) for k, v in kinds.items()} for s, kinds in by_symbol.items()},
           "aggregates": {k: sorted(v) for k, v in structs.items()}}
    (E / "harvest.json").write_text(json.dumps(out, indent=1))
    conflicts = {s: k for s, kinds in by_symbol.items() for k, v in kinds.items() if len(v) > 1}
    print(json.dumps({"solved_sources_used": len(sources),
                      "symbols_with_signature": sum("signature" in k for k in by_symbol.values()),
                      "symbols_with_global_decl": sum("global" in k for k in by_symbol.values()),
                      "symbols_with_prototype": sum("prototype" in k for k in by_symbol.values()),
                      "aggregate_names": len(structs),
                      "symbols_with_conflicting_decls": len(conflicts)}, indent=1))


if __name__ == "__main__":
    main()

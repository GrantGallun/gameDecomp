"""Audit a claimed match for the one thing that can silently demote it: a type only a `.c` declares.

`tools/claude` builds `ctx.c` by running `m2ctx.py` on `C_FILE="src/${RELATIVE_DIR}.c"` -- the project's
OWN fully-matched source file -- and passes it to m2c as `--context`. For a target that is 100%
decompiled, the declarations in that context ARE the reference answer. So a draft m2c produced at
bootstrap time may already contain the reference struct layout, and a byte-exact match built on it is
not independent capability, whatever its score says.

The mechanical test needs no judgement about intent:

  * collect every non-primitive type name the source mentions,
  * ask `header_admission.declaring_header` where it is declared,
  * a name that resolves to a header is at worst `header-assisted`,
  * a name that resolves to NOTHING but is declared in a project `.c` means the source carries a
    layout that was handed to the solver from the reference decompilation.

    python3 -m eval.match_claim_audit --functions a,b,c
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import header_admission as ha                                     # noqa: E402

REPO = Path.home() / "decomp/sbk1"
TYPE_USE = re.compile(r"\b([A-Z]\w{2,})\b")
DECLARED_IN_C = re.compile(r"^\s*\}\s*{name}\s*;|^\s*typedef\b[^;{{}}]*\b{name}\b\s*;", re.M)


def c_declarations(repo: Path, name: str) -> list[str]:
    """Project `.c` files that declare `name` as a type (the file-local spelling headers never carry)."""
    pattern = re.compile(rf"\}}\s*{re.escape(name)}\s*;|"
                         rf"\btypedef\b[^;{{}}]*?\b{re.escape(name)}\b\s*(?:\[[^\];{{}}]*\])?\s*;")
    hits = []
    for src in sorted((repo / "src").rglob("*.c")):
        try:
            if pattern.search(src.read_text(errors="replace")):
                hits.append(str(src.relative_to(repo)))
        except OSError:
            continue
    return hits


def audit(repo: Path, name: str, source: str) -> dict:
    used = sorted({m.group(1) for m in TYPE_USE.finditer(source)})
    known = getattr(audit, "_known", None)
    if known is None:
        known = audit._known = ha.__dict__.setdefault("_PRIM", set())
    row = {"function": name, "types": {}}
    for t in used:
        if t in ha.PRIMITIVE or t.startswith(("D_", "Unk", "func_", "M2C")):
            continue
        header = ha.declaring_header(repo, t)
        if header:
            row["types"][t] = {"header": header}
    # Names with no header: does any project .c declare them as a type?
    headerless = [t for t in used
                  if t not in ha.PRIMITIVE and t not in row["types"] and not t.startswith(("D_", "Unk"))]
    for t in headerless:
        cs = c_declarations(repo, t)
        if cs:
            row["types"][t] = {"header": None, "declared_in_c": cs[:4],
                               "verdict": "reflects-a-project-.c-declaration"}
    row["header_backed"] = sorted(t for t, v in row["types"].items() if v.get("header"))
    row["project_c_backed"] = sorted(t for t, v in row["types"].items() if not v.get("header"))
    # MENTIONING A TYPE IS NOT THE SAME AS TAKING ITS LAYOUT. What decides the tier is whether the
    # source dereferences a MEMBER through a header-declared type -- `RacePlayer *p; ... p->field` --
    # because that is the header supplying the offsets. A source that names the type only in a
    # prototype has taken nothing from it. Measured separately so the weaker signal cannot be quoted
    # as the stronger one.
    member_access = []
    for t in row["header_backed"]:
        for decl in re.finditer(rf"\b{re.escape(t)}\s*\*+\s*(?P<var>[A-Za-z_]\w*)\s*[;=,)]", source):
            if re.search(rf"\b{re.escape(decl.group('var'))}\s*->", source):
                member_access.append(t)
                break
    row["member_access_through_header_type"] = sorted(set(member_access))
    # Diagnostic for the mentions-only rows: a source with no `->` anywhere takes no layout from
    # anything, so its SOLVED status cannot be a header in disguise. Where this is non-zero on a
    # mentions-only row, the regex missed a declaration shape and the row needs looking at.
    row["arrow_uses"] = source.count("->")
    row["tier_ceiling"] = ("header-assisted" if row["project_c_backed"] or member_access
                           else "mentions-a-header-type" if row["header_backed"]
                           else "unqualified")
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--functions", default="")
    ap.add_argument("--types", default="",
                    help="comma-separated type names: report where each is declared, if anywhere")
    ap.add_argument("--from-ledger", metavar="GLOB", default="",
                    help="audit the source that ACTUALLY MATCHED in each object_exact ledger node, "
                         "instead of the workspace's base.c")
    ap.add_argument("--from-db", action="store_true",
                    help="whole-set check: audit the winning exact attempt's source_code for EVERY "
                         "matched function in the knowledge base")
    ap.add_argument("--db", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--results", type=Path, default=ROOT / "eval/results")
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/match-claim-audit-20260917.json")
    args = ap.parse_args(argv)

    rows = {}
    if args.from_db:
        # THE WHOLE-SET CHECK. `status.py` splits the tiers by reading a strategy LABEL; this reads the
        # source that the object accepted, for every function the knowledge base calls exact. The two
        # answers must agree before either is quoted, and where they disagree this is the one with the
        # evidence attached.
        import sqlite3
        conn = sqlite3.connect(str(args.db))
        # Per function, the exact attempt with the highest score, latest run as the tiebreak. Any exact
        # attempt is a valid witness of what matched; this picks one deterministically.
        winning = conn.execute(
            "select f.name, a.source_code, a.strategy, a.score from attempts a "
            "join functions f on f.addr = a.func_addr "
            "where a.exact = 1 and a.source_code is not null and a.source_code <> '' "
            "order by a.score desc, a.id desc").fetchall()
        seen, tally, no_source = {}, collections.Counter(), []
        for name, source, strategy, score in winning:
            if name in seen:
                continue
            seen[name] = True
            r = audit(args.repo, name, source)
            r["strategy"] = strategy
            r["score"] = score
            r["label_says_header_assisted"] = bool(strategy and "project-header" in strategy)
            rows[name] = r
            tally[r["tier_ceiling"]] += 1
        exact_db = {r[0] for r in conn.execute(
            "select distinct f.name from attempts a join functions f on f.addr = a.func_addr "
            "where a.exact = 1")}
        # The cross-tabulation that matters: `status.py`'s own `recovered` definition, intersected with
        # the source audit. A recovered source IS the reference decompilation, so it will use project
        # types by construction; counting it again as "header-assisted" would double-book a tier that
        # is already excluded from SOLVED.
        recovered = {r[0] for r in conn.execute(
            "select distinct f.name from attempts a join functions f on f.addr = a.func_addr "
            "where a.exact = 1 and (a.strategy like '%history-recovery%' "
            "or a.strategy like '%historical-provenance%' "
            "or a.strategy like '%symbol-restoration%')")}
        label_solved = {n for n, r in rows.items()
                        if not r["label_says_header_assisted"] and n not in recovered}
        cross = collections.Counter()
        for name in exact_db:
            r = rows.get(name)
            if r is None:
                continue
            tier = ("recovered" if name in recovered
                    else "header-assisted-by-label" if r["label_says_header_assisted"]
                    else "SOLVED-by-label")
            cross[(tier, r["tier_ceiling"])] += 1
        affected = sorted(n for n in label_solved if rows[n]["tier_ceiling"] == "header-assisted")
        no_source = sorted(exact_db - set(rows))
        report = {"exact_functions_in_db": len(exact_db), "audited": len(rows),
                  "no_source_code_on_any_exact_attempt": len(no_source),
                  "ceiling": dict(tally),
                  "cross_tab": {f"{k[0]} / {k[1]}": v for k, v in sorted(cross.items())},
                  "labelled_SOLVED_but_source_says_header_assisted": len(affected),
                  "labelled_SOLVED_and_source_takes_no_layout": sum(
                      1 for n in label_solved
                      if rows[n]["tier_ceiling"] == "unqualified" or rows[n]["arrow_uses"] == 0),
                  "affected_sample": affected[:30],
                  "no_source_names": no_source[:40]}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"report": report, "rows": rows}, indent=2, sort_keys=True),
                            encoding="utf-8")
        print(json.dumps(report, indent=2))
        print(f"wrote {args.out}")
        return 0

    if args.from_ledger:
        # Audit the source that MATCHED, not `base.c`. A cohort node's winning source is a different
        # artifact (`...-artifacts/<stamp>-<name>.best.c`), and auditing the seed draft instead would
        # answer a question nobody asked: the draft is where a run starts, the best source is what the
        # object actually accepted.
        from eval.cohort_reconcile import cohort_nodes
        nodes = cohort_nodes(args.results, args.from_ledger)
        print(f"object_exact ledger nodes: {len(nodes)}")
        tally = collections.Counter()
        for node in nodes:
            name, source = node["function"], node["source"]
            if not source.strip():
                rows[name] = {"status": "no-source-in-node", "ledger": node["ledger"]}
                tally["no-source-in-node"] += 1
                continue
            r = audit(args.repo, name, source)
            r["ledger"] = node["ledger"]
            r["score"] = node.get("score")
            rows[name] = r
            tally[r["tier_ceiling"]] += 1
            flag = ""
            if r["project_c_backed"]:
                flag = f"  .c-only={r['project_c_backed']}"
            elif r["header_backed"]:
                flag = f"  header={r['header_backed']}"
            print(f"  {name:<44} {r['tier_ceiling']:<14}{flag}")
        print(dict(tally))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    if args.types:
        for t in [n.strip() for n in args.types.split(",") if n.strip()]:
            header, cs = ha.declaring_header(args.repo, t), c_declarations(args.repo, t)
            verdict = ("header" if header else
                       "project-.c-only" if cs else "not-declared-anywhere")
            rows[t] = {"header": header, "declared_in_c": cs[:4], "verdict": verdict}
            print(f"{t:<34} {verdict:<22} {header or (cs[0] if cs else '')}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    for name in [n.strip() for n in args.functions.split(",") if n.strip()]:
        ws = args.repo / "nonmatchings" / name
        base = ws / "base.c"
        if not base.is_file():
            rows[name] = {"status": "no-base.c"}
            continue
        rows[name] = audit(args.repo, name, base.read_text(errors="replace"))
        r = rows[name]
        print(f"{name}: ceiling={r['tier_ceiling']}")
        if r["header_backed"]:
            print(f"   header-backed    : {r['header_backed']}")
        if r["project_c_backed"]:
            print(f"   .c-only-backed   : {r['project_c_backed']}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""How well does IDO's own line table locate the edit? (measurement for the next logic round)

    python3 attribution_check.py [--split exam] [--limit 200]

For each explain task: compile the shown (damaged) function alone in its checked context, read IDO's address->line
records (`objdump -dl` on the unstripped object, the same records solver/source_attribution.py uses), align them with
the normalized listing, take the lines of the instructions that differ from the target, and ask whether they include
a line the reference edit touches (+-1 for insertions). Reports hit rate and how many lines the attribution names,
i.e. how much it would narrow the search for the right line.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import re
import subprocess
from pathlib import Path

import public_plant as pp
from solver import line_map as line_map_module

from eval import logic_tasks as lt

L = Path.home() / "decomp/experiments/edit-capability-20261002/logic-pilot-v3"
CTX = Path.home() / "decomp/experiments/edit-capability-20261002/public/context-v3.jsonl"
HEADER = re.compile(r"^([0-9a-f]+) <([^>]+)>:$")
LINE = re.compile(r"^(.+?):(\d+)(?:\s+\(discriminator \d+\))?$")
INSN = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(.+)$")


def line_records(obj: Path, name: str) -> list[int | None]:
    """Source line per instruction of `name`, in address order."""
    out = subprocess.run([pp.ws_objdump.find_objdump_executable(), "-dl", *pp.ws_objdump.OBJDUMP_ARGS[1:], str(obj)],
                         capture_output=True, text=True, check=True).stdout
    inside, line, rows = False, None, []
    for entry in out.splitlines():
        m = HEADER.match(entry)
        if m:
            inside, line = m.group(2) == name, None
            continue
        if not inside:
            continue
        m = LINE.match(entry.strip())
        if m:
            line = int(m.group(2))
            continue
        if INSN.match(entry):
            rows.append(line)
    return rows


def edit_lines(script: str) -> set[int]:
    lines = set()
    for raw in script.splitlines():
        m = lt.EDIT.match(raw.strip())
        if not m:
            continue
        if m.group(2):
            lines.add(int(m.group(2)))
        elif m.group(3) is not None:
            n = int(m.group(3))
            lines |= {n, n + 1}            # an insertion sits between n and n+1
        else:
            lines.add(int(m.group(4)))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="exam")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--gaps", action="store_true", help="also name the lines around missing instructions")
    ap.add_argument("--decls", action="store_true", help="also name declarations whose type moves differing rows")
    ap.add_argument("--stmts", action="store_true", help="statement-deletion influence for rows the line table misses")
    ap.add_argument("--stmts-always", action="store_true",
                    help="run deletion influence on every task: verifies line-table claims the optimizer moved")
    ap.add_argument("--probes", action="store_true", help="insertion probes to place missing instructions")
    ap.add_argument("--probe-all", action="store_true", help="match probes to EVERY differing span, not only gaps")
    a = ap.parse_args()
    tasks = [t for t in map(json.loads, open(L / "logic-v3/tasks.jsonl"))
             if t["kind"] == "logic-explain" and t["split"] == a.split][:a.limit]
    rows = {r["id"]: r for r in map(json.loads, open(CTX))}
    bmap = pp.builds()
    c = collections.Counter()
    named = []
    for t in tasks:
        row = rows[t["row_id"]]
        build = bmap[(f"{row['repository']}.{row['variant']}", row["file"])]
        text = row["context"] + "\n" + row["perturbed_fn"]
        offset = (row["context"] + "\n").count("\n")          # function line 1 is file line offset + 1
        obj = pp.compile_tu(build, text, "attr")
        if obj is None:
            c["does-not-compile"] += 1
            continue
        listing = pp.listing(obj, row["function"])
        lines = line_records(obj, row["function"])
        if listing != row["current"] or len(lines) < len(listing):
            c["listing-mismatch"] += 1
            continue
        from solver import line_map
        dump = subprocess.run([pp.ws_objdump.find_objdump_executable(), "-dl", *pp.ws_objdump.OBJDUMP_ARGS[1:],
                               str(obj)], capture_output=True, text=True, check=True).stdout
        lm = line_map.build(dump, row["function"], listing, line_offset=offset)
        att = lm.attribute(row["target"], listing)
        n_fn = len(row["perturbed_fn"].split("\n"))
        attributed = {n for n in att["changed"] if 1 <= n <= n_fn}
        if a.gaps:
            for lo, hi in att["gaps"]:
                attributed |= {n for n in (lo, hi) if n is not None and 1 <= n <= n_fn}
        def compile_listing(text):
            o = pp.compile_tu(build, text, "decl")
            return pp.listing(o, row["function"]) if o else None
        if a.decls:
            influence = line_map.declaration_influence(row["context"], row["perturbed_fn"], row["function"],
                                                       compile_listing)
            attributed |= set(line_map.with_declarations(att, influence)["declarations"])
        differing = {r for rows_ in att["changed"].values() for r in rows_}
        if a.stmts and (a.stmts_always or differing - {r for n in attributed for r in lm.rows_of(n)}):
            # Only regions not already explained by the line table: the intervention is the expensive fallback.
            for first, last in line_map.regions_for(differing, line_map.statement_influence(
                    row["context"], row["perturbed_fn"], row["function"], compile_listing)):
                attributed |= set(range(first, last + 1))
        if a.probes and (att["diff_spans"] if a.probe_all else att["gap_rows"]):
            probes = line_map.insertion_probes(row["context"], row["perturbed_fn"], row["function"], compile_listing,
                                               target=row["target"])
            for span in (att["diff_spans"] if a.probe_all else att["gap_spans"]):
                for q in line_map.insertion_points(span, probes):
                    attributed |= {q, q + 1}
        kind = line_map.nonlocal_kind(row["target"], listing)
        if kind:
            c[f"nonlocal:{kind}"] += 1
        want = edit_lines(t["completion"])
        c["measured"] += 1
        verdict = "none" if not attributed else ("hit" if attributed & want else "miss")
        c[verdict] += 1
        c[f"{t['class']}:{verdict}"] += 1
        named.append(len(attributed))
        c["function-lines"] += len(row["perturbed_fn"].split("\n"))
    print(json.dumps(dict(c)))
    print("skipped interventions:", dict(line_map_module.SKIPPED))
    if named:
        named.sort()
        print(f"lines named per task: median {named[len(named) // 2]}, mean {sum(named) / len(named):.1f}; "
              f"mean function length {c['function-lines'] / max(1, c['measured']):.1f}")


if __name__ == "__main__":
    main()

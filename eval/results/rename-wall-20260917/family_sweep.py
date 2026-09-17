"""Is the target's property state reachable at all from the existing generator set?

The residual on all five 99.936 cases is one two-instruction pair. Two properties of a compiled dump:

    ORDER   `move s2,zero` immediately followed by `move s3,zero`
    REG     `addiu s2,s2,2` -- the first loop increments tileIndex, so s2 holds tileIndex

The target has both. Every source form measured by hand so far has exactly one: with tileIndex in s3 the
emission is tileIndex-then-offset (ORDER), and with tileIndex in s2 it is offset-then-tileIndex (REG).
`regalloc_search` is a beam and drops candidates, so "the beam did not find it" is weak evidence.
This does a breadth-first enumeration of the same generator set with no beam and no model: every variant
of every family, then every variant of every variant, deduplicated by source text.

    python3 eval/results/rename-wall-20260917/family_sweep.py <function> <attempt_id> [--depth N] [--cap N]

Writes every compiled variant to `family-sweep-<function>.json` as it lands, so the run is resumable by
reading that file and a partial run is still a result.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import regalloc_mutations, regalloc_signature, workspace  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"
OUT = Path(__file__).resolve().parent


def properties(dump: str) -> dict:
    lines = [line.split() for line in dump.splitlines()]
    order = any(lines[i] == ["move", "s2,zero"] and lines[i + 1] == ["move", "s3,zero"]
                for i in range(len(lines) - 1))
    reverse = any(lines[i] == ["move", "s3,zero"] and lines[i + 1] == ["move", "s2,zero"]
                  for i in range(len(lines) - 1))
    return {"order": order, "reverse": reverse, "reg_s2": any(row == ["addiu", "s2,s2,2"] for row in lines)}


def main() -> int:
    name = sys.argv[1]
    attempt = int(sys.argv[2])
    depth = int(sys.argv[sys.argv.index("--depth") + 1]) if "--depth" in sys.argv else 2
    cap = int(sys.argv[sys.argv.index("--cap") + 1]) if "--cap" in sys.argv else 1500
    conn = sqlite3.connect(DB)
    source = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    ws = workspace.bootstrap(REPO, name)
    target = (ws / "target_object_dump_normalized.s").read_text(errors="replace")

    def compile_and_report(candidate: str, label: str, kind: str, parent: str) -> dict:
        att = workspace.score(ws, REPO, name, candidate, conn=None)
        row = {"label": label, "kind": kind, "parent": parent, "compiled": bool(att.compiled),
               "diff": att.diff or ""}
        if att.compiled:
            dump = (ws / f"{name}_object_dump_normalized.s").read_text(errors="replace")
            report = regalloc_signature.compare(target, dump)
            row.update(exact=bool(att.exact), score=att.score, gradient=list(report.gradient),
                       **properties(dump))
        return row

    base = compile_and_report(source, "baseline", "baseline", "")
    print(f"{name} attempt {attempt}  target={properties(target)}", flush=True)
    print(f"  baseline {base.get('gradient')} order={base.get('order')} reg_s2={base.get('reg_s2')} "
          f"exact={base.get('exact')}", flush=True)
    records = [dict(base, source=source)]
    counts: Counter = Counter()
    if base.get("order") and base.get("reg_s2"):
        counts[(True, True, base.get("reverse", False))] += 1
    frontier = [(source, base.get("gradient") or [0, 0, 0], "baseline", base.get("diff", ""))]
    seen = {source}
    started = time.time()
    outcome = "depth"
    for level in range(1, depth + 1):
        nxt = []
        for parent_source, _parent_gradient, parent_label, parent_diff in frontier:
            for label, kind, variant in regalloc_mutations.variants(parent_source, name, parent_diff):
                if variant in seen:
                    continue
                seen.add(variant)
                if len(records) >= cap:
                    outcome = "cap"
                    break
                row = compile_and_report(variant, label, kind, parent_label)
                row["source"] = variant
                records.append(row)
                nxt.append((variant, row.get("gradient") or [9e9, 0, 0], label, row.get("diff", "")))
                if row.get("compiled"):
                    counts[(row["order"], row["reg_s2"], row["reverse"])] += 1
                    if row["order"] and row["reg_s2"]:
                        print(f"  *** GOAL STATE depth {level}: {label} ({kind}) from {parent_label} "
                              f"exact={row.get('exact')} grad={row['gradient']}", flush=True)
                    if row.get("exact"):
                        print(f"  *** EXACT depth {level}: {label} ({kind}) score={row['score']}", flush=True)
                        outcome = "exact"
                if len(records) % 50 == 0:
                    print(f"  [{len(records)}] d{level} {label:<40} {row.get('gradient')} "
                          f"order={row.get('order')} reg_s2={row.get('reg_s2')} "
                          f"({time.time() - started:.0f}s)", flush=True)
                (OUT / f"family-sweep-{name}-{attempt}.json").write_text(
                    json.dumps(records, indent=1), encoding="utf-8")
            if outcome in ("exact", "cap"):
                break
        frontier = nxt
        print(f"depth {level}: frontier {len(frontier)}  records {len(records)}", flush=True)
        if outcome in ("exact", "cap") or not frontier:
            break
    (OUT / f"family-sweep-{name}-{attempt}.json").write_text(
        json.dumps(records, indent=1), encoding="utf-8")
    print(f"outcome={outcome} compiles={len(records)} seconds={time.time() - started:.0f}")
    print("(order, reg_s2, reverse) -> count:", dict(counts))
    goal = [r for r in records if r.get("order") and r.get("reg_s2")]
    print(f"goal-state rows: {len(goal)}")
    for row in goal[:20]:
        print("   ", row["kind"], row["label"], row["gradient"], "exact" if row.get("exact") else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

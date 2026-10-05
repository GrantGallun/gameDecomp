"""Coverage: the share of a frozen panel the system makes exact, and what reaching it cost.

    python3 -m eval.coverage PANEL RUN.jsonl [--budgets 12,24,48] [--cost compiles]
    python3 -m eval.coverage PANEL BEFORE.jsonl --against AFTER.jsonl [--heldout]

Coverage is the headline; cost only orders changes of equal coverage. A change is accepted when it gains at least one
case and loses none, or covers exactly the same cases at a lower TOTAL panel cost. Any lost case refuses it, whatever
it gains (the rule of eval/posttraining_gate.py), and so does a run missing any panel case.

`compiles` in site_edits.search records counts candidate children: the root compile and source-attribution work are
not in it. Read it as a candidate budget, not a meter of compiler invocations.

A panel is a cases .jsonl whose rows carry `id` (and `class`, for the per-class table). A run is a .jsonl with one row
per case: `id`, `exact`, and its cost, either top-level (`compiles`, `tokens`) or under `cost`. `exact` must come from
the caller's oracle verdict; nothing here compiles. A panel case with no run row counts as NOT covered and is listed,
never dropped: a run that silently skipped its hard cases would otherwise report a higher coverage than it earned.
Rows naming a case outside the panel, or naming one twice, are refused.

Coverage@budget reads each exact case's cost-to-exact from one run. That equals a run at the smaller budget only when
the search order does not depend on the budget; site_edits.search spends `per_step` compiles per level whatever the
budget, so the curve is exact for it. Say so before reading the curve of any other searcher.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path


class CoverageError(ValueError):
    pass


def load_panel(path) -> dict:
    """{"ids": [...], "classes": {id: class}, "sha256": digest of the file's bytes}."""
    data = Path(path).read_bytes()
    ids, classes = [], {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["id"] in classes:
            raise CoverageError(f"panel names {row['id']!r} twice")
        ids.append(row["id"])
        classes[row["id"]] = row.get("class", "")
    return {"ids": ids, "classes": classes, "sha256": hashlib.sha256(data).hexdigest()}


def load_run(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def cost_of(row: dict, cost: str) -> float:
    if isinstance(row.get("cost"), dict) and cost in row["cost"]:
        return float(row["cost"][cost])
    if cost in row:
        return float(row[cost])
    raise CoverageError(f"run row {row.get('id')!r} records no {cost!r} cost")


def _index(panel: dict, rows: list[dict]) -> dict[str, dict]:
    known = set(panel["ids"])
    out: dict[str, dict] = {}
    for row in rows:
        case = row.get("id")
        if case not in known:
            raise CoverageError(f"run row {case!r} is not a case of this panel")
        if case in out:
            raise CoverageError(f"run names {case!r} twice")
        out[case] = row
    return out


def coverage(panel: dict, rows: list[dict], *, cost: str = "compiles", budgets=()) -> dict:
    by_id = _index(panel, rows)
    n = len(panel["ids"])
    exact = [c for c in panel["ids"] if by_id.get(c, {}).get("exact")]
    spent = {c: cost_of(by_id[c], cost) for c in exact}
    per_class = collections.defaultdict(lambda: [0, 0])
    for c in panel["ids"]:
        per_class[panel["classes"][c]][1] += 1
        per_class[panel["classes"][c]][0] += c in spent
    total = sum(cost_of(r, cost) for r in by_id.values())
    return {
        "panel_sha256": panel["sha256"], "n": n, "covered": len(exact), "coverage": len(exact) / n if n else 0.0,
        "missing_rows": [c for c in panel["ids"] if c not in by_id],
        "cost": cost, "total_cost": total,
        "at_budget": {str(b): sum(1 for v in spent.values() if v <= b) for b in budgets},
        "per_class": {k: {"covered": v[0], "n": v[1]} for k, v in sorted(per_class.items())},
        "exact_ids": exact,
    }


def compare(panel: dict, before: list[dict], after: list[dict], *, cost: str = "compiles") -> dict:
    """Paired: which cases each arm covers that the other does not, and the verdict for adopting `after`.

    Cost is the TOTAL over the panel, failures included: an arm that saves one compile on a solved case and spends
    10,000 on an unsolved one is not cheaper (audit 2026-10-03, docs/model-capability-training-audit-20261003.md).
    Cost on the cases both cover is kept as a diagnostic only. Either run missing a panel case refuses: a verdict
    needs complete observations.
    """
    b, a = _index(panel, before), _index(panel, after)
    missing_b = [c for c in panel["ids"] if c not in b]
    missing_a = [c for c in panel["ids"] if c not in a]
    eb = {c for c in panel["ids"] if b.get(c, {}).get("exact")}
    ea = {c for c in panel["ids"] if a.get(c, {}).get("exact")}
    gained, lost = sorted(ea - eb), sorted(eb - ea)
    both = sorted(ea & eb)
    total_before = sum(cost_of(r, cost) for r in b.values())
    total_after = sum(cost_of(r, cost) for r in a.values())
    if missing_b or missing_a:
        verdict = "refuse: incomplete run"
    elif lost:
        verdict = "refuse: loses covered cases"
    elif gained:
        verdict = "accept: covers more"
    elif total_after < total_before:
        verdict = "accept: same coverage, cheaper"
    else:
        verdict = "keep baseline: no gain"
    return {"panel_sha256": panel["sha256"], "n": len(panel["ids"]),
            "covered_before": len(eb), "covered_after": len(ea), "gained": gained, "lost": lost,
            "cost": cost, "total_cost_before": total_before, "total_cost_after": total_after,
            "cost_on_both_before": sum(cost_of(b[c], cost) for c in both),
            "cost_on_both_after": sum(cost_of(a[c], cost) for c in both),
            "missing_before": missing_b, "missing_after": missing_a, "verdict": verdict}


def _print_coverage(r: dict, heldout: bool) -> None:
    print(f"panel {r['panel_sha256'][:12]}  coverage {r['covered']}/{r['n']} ({100 * r['coverage']:.1f}%)  "
          f"total {r['cost']} {r['total_cost']:g}")
    if r["at_budget"]:
        print("  at budget: " + "  ".join(f"<={b}: {v}/{r['n']}" for b, v in r["at_budget"].items()))
    for cls, v in r["per_class"].items():
        print(f"  {cls or '-':24} {v['covered']}/{v['n']}")
    if r["missing_rows"]:
        print(f"  {len(r['missing_rows'])} panel cases have no run row (counted uncovered)"
              + ("" if heldout else ": " + ", ".join(r["missing_rows"][:10])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("panel")
    ap.add_argument("run")
    ap.add_argument("--against", help="a second run: paired comparison, verdict for adopting it")
    ap.add_argument("--cost", default="compiles")
    ap.add_argument("--budgets", default="")
    ap.add_argument("--heldout", action="store_true", help="print counts only, never case ids")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    panel = load_panel(a.panel)
    if a.against:
        r = compare(panel, load_run(a.run), load_run(a.against), cost=a.cost)
        if a.json:
            print(json.dumps(r if not a.heldout else {k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}))
            return 0
        print(f"panel {r['panel_sha256'][:12]}  {r['covered_before']}/{r['n']} -> {r['covered_after']}/{r['n']}  "
              f"gained {len(r['gained'])} lost {len(r['lost'])}  "
              f"total {r['cost']}: {r['total_cost_before']:g} -> {r['total_cost_after']:g}  "
              f"(on cases both cover: {r['cost_on_both_before']:g} -> {r['cost_on_both_after']:g})")
        if r["missing_before"] or r["missing_after"]:
            print(f"  missing rows: before {len(r['missing_before'])}, after {len(r['missing_after'])}")
        if not a.heldout:
            for c in r["gained"]:
                print("  + " + c)
            for c in r["lost"]:
                print("  - " + c)
        print(r["verdict"])
        return 0
    budgets = [int(x) for x in a.budgets.split(",") if x]
    r = coverage(panel, load_run(a.run), cost=a.cost, budgets=budgets)
    if a.json:
        print(json.dumps({k: v for k, v in r.items() if not (a.heldout and k in ("exact_ids", "missing_rows"))}))
    else:
        _print_coverage(r, a.heldout)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Make the fresh-cohort exacts visible AND counted, by re-verifying them rather than trusting a ledger.

`fresh_run_v1.py` writes its own ledger and never touches kb-sbk1.sqlite, so a cohort run that produces
object-exact functions leaves `eval.status` unchanged -- measured on v9: 2 exact of 12 cold functions and
byte-identical status output before and after. That is bookkeeping, not a tier difference, and the fix is
to put the attempts where every other attempt lives.

Two rules this follows, both from the operator's criterion:

  RE-VERIFY, DO NOT BELIEVE.  A ledger row saying `object_exact` is a claim, not a measurement. Every
  candidate here is compiled again through the ordinary path and only an object comparison's verdict is
  logged. If a row does not reproduce, that is a finding about the ledger and is reported.

  DE-DUPLICATE FIRST.  A function already exact in the knowledge base is skipped -- six of the "40" in
  the earlier boundary sweep turned out to be exactly that, with 13-37 exact rows each.

    python3 eval/cohort_reconcile.py [--out DIR] [--limit N]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import workspace                                              # noqa: E402

REPO = Path.home() / "decomp" / "sbk1"
DB = Path.home() / "decomp" / "kb-sbk1.sqlite"


def _source_text(node: dict) -> str:
    """The node's C text.

    `source` is a PATH to the run's artifact (`...-artifacts/<stamp>-<name>.best.c`), not the C itself.
    Compiling it as C gives `Syntax Error` at line 1 for every node, which is how this was found: the
    first version of this script believed the field name instead of reading what it held.
    """
    raw = node.get("source") or ""
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        try:
            return path.read_text(errors="replace")
        except OSError:
            return ""
    return raw


def cohort_nodes(results: Path, pattern: str = "failure-coverage-fresh-paired-*.json") -> list[dict]:
    out = []
    for path in sorted(results.glob(pattern)):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        nodes = d.get("nodes") or {}
        pairs = nodes.items() if isinstance(nodes, dict) else ((None, n) for n in nodes)
        for key, node in pairs:
            if not isinstance(node, dict):
                continue
            state = node.get("state") or node.get("status")
            name = key or node.get("function") or node.get("name")
            if name and (node.get("object_exact") or state == "object_exact"):
                out.append({"function": str(name), "ledger": path.name,
                            "source": _source_text(node), "score": node.get("score")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=ROOT / "eval/results")
    ap.add_argument("--ledger-glob", default="failure-coverage-fresh-paired-*.json",
                    help="which ledgers to reconcile. The round-1 run used the fresh-paired family; "
                         "eval/ledger_inventory.py named rom-paired as the next one")
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/cohort-reconcile-20260917")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    nodes = cohort_nodes(args.results, args.ledger_glob)
    if args.limit:
        nodes = nodes[:args.limit]
    print(f"cohort nodes claiming object_exact: {len(nodes)}", flush=True)

    conn = sqlite3.connect(str(DB))
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    for item in nodes:
        name = item["function"]
        if name in state:
            continue
        row: dict = {"function": name, "ledger": item["ledger"], "ledger_score": item["score"]}
        addr = conn.execute("select addr from functions where name=?", (name,)).fetchone()
        if addr is None:
            row["status"] = "not-in-kb"
            state[name] = row
            continue
        existing = conn.execute("select count(*) from attempts where func_addr=? and exact=1",
                                (addr[0],)).fetchone()[0]
        row["kb_exact_rows_before"] = existing
        if existing:
            row["status"] = "already-counted"
            state[name] = row
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            print(f"{name:<46} already-counted ({existing} exact rows)", flush=True)
            continue
        if not item["source"]:
            row["status"] = "ledger-has-no-source"
            state[name] = row
            state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            print(f"{name:<46} ledger-has-no-source", flush=True)
            continue
        try:
            ws = workspace.bootstrap(REPO, name)
            att = workspace.score(ws, REPO, name, item["source"], conn=conn, func=name,
                                  strategy=f"cohort-reconcile:{item['ledger']}", iteration=0,
                                  run_kind="cohort-reconcile")
            row.update(compiled=bool(att.compiled), exact=bool(att.exact), score=att.score,
                       receipt=att.receipt_id)
            row["status"] = ("REPRODUCED-EXACT" if att.exact else
                             "compiled-not-exact" if att.compiled else "not-compiling")
        except Exception as exc:                                         # noqa: BLE001
            row["status"] = "raised"
            row["error"] = f"{type(exc).__name__}: {exc}"
        state[name] = row
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{len(state)}/{len(nodes)}] {name:<44} {row['status']} score={row.get('score')}",
              flush=True)

    summary = {"population": len(nodes), "recorded": len(state),
               "status_counts": dict(collections.Counter(r.get("status") for r in state.values())),
               "reproduced_exact": sorted(n for n, r in state.items()
                                          if r.get("status") == "REPRODUCED-EXACT")}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "reproduced_exact"}, indent=2))
    print("reproduced exact:", ", ".join(summary["reproduced_exact"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

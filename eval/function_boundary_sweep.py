"""Certify the FUNCTION boundary for every residual whose normalized assembly already matches.

`eval/function_certificate_census.py` found 122 certificates across 40 functions with
`normalized_assembly_exact: true` and `exact: false`, of which only some carried a function-boundary
verdict. The 11 that did are now a counted tier. The rest were never asked the question.

The question is mechanical and answers itself: `solver/function_boundary.py` line 204 requires

    candidate_linked == target_linked == rom_data[offset:offset + size]

so either the candidate's function bytes reproduce the ROM's or they do not. One compile per function,
no model, and the verdict comes from the ROM -- not from a score, not from a judgement call.

    python3 eval/function_boundary_sweep.py [--out DIR] [--limit N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import workspace                                              # noqa: E402

REPO = Path.home() / "decomp" / "sbk1"
DB = Path.home() / "decomp" / "kb-sbk1.sqlite"


def candidates(repo: Path) -> dict[str, str]:
    """Functions whose best certificate has normalized_assembly_exact true and exact false.

    The certificate is the authority on which functions these are, so the sweep is driven by the
    receipts on disk rather than by a query that might disagree with them.
    """
    out: dict[str, str] = {}
    for path in (repo / "nonmatchings").glob("*/*.verification.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if d.get("kind") != "mips_object_section_certificate" or d.get("exact"):
            continue
        if not d.get("normalized_assembly_exact"):
            continue
        if (d.get("function_boundary") or {}).get("function_exact"):
            continue                                       # already counted in the tier
        out[path.parent.name] = path.name
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/function-boundary-sweep-20260917")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    names = candidates(REPO)
    if args.limit:
        names = dict(list(names.items())[:args.limit])
    print(f"functions with a normalized-exact residual, no function verdict yet: {len(names)}",
          flush=True)

    conn = sqlite3.connect(str(DB))
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    started = time.time()
    for name in sorted(names):
        if name in state:
            continue
        row: dict = {"function": name, "certificate": names[name]}
        try:
            found = conn.execute(
                "select a.source_code, a.id from attempts a join functions f on f.addr = a.func_addr "
                "where f.name = ? and a.compiled = 1 order by a.score desc, a.id limit 1",
                (name,)).fetchone()
            if not found or not found[0]:
                row["status"] = "no-compiling-source"
                state[name] = row
                continue
            ws = workspace.bootstrap(REPO, name)
            att = workspace.score(ws, REPO, name, found[0], conn=None)
            verification = att.verification or {}
            boundary = verification.get("function_boundary") or {}
            row.update(compiled=bool(att.compiled), exact=bool(att.exact), score=att.score,
                       object_status=verification.get("status"),
                       function_exact=bool(boundary.get("function_exact")),
                       boundary_status=boundary.get("status"),
                       boundary_error=boundary.get("error"),
                       schema=boundary.get("schema_version"),
                       target_trailing=boundary.get("target_trailing_bytes"),
                       candidate_trailing=boundary.get("candidate_trailing_bytes"))
            row["status"] = ("function_exact" if row["function_exact"]
                             else boundary.get("status") or verification.get("status") or "no-certificate")
        except Exception as exc:                                         # noqa: BLE001
            row["status"] = "raised"
            row["error"] = f"{type(exc).__name__}: {exc}"
        state[name] = row
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{len(state)}/{len(names)}] {name:<46} function_exact={row.get('function_exact')} "
              f"object={row.get('object_status')} boundary={row.get('boundary_status')} "
              f"{str(row.get('boundary_error') or '')[:60]}", flush=True)

    hit = sorted(n for n, r in state.items() if r.get("function_exact"))
    summary = {"population": len(names), "recorded": len(state),
               "function_exact": len(hit), "function_exact_functions": hit,
               "status_counts": dict(collections.Counter(r.get("status") for r in state.values()))}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "function_exact_functions"}, indent=2))
    print("function-exact:", ", ".join(hit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

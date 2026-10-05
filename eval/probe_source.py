"""Compile one hand-written candidate against a function's target and show what still differs.

    python -m eval.probe_source FUNCTION candidate.c [more.c ...]

For reading residuals: prints exactness, site_edits.gradient, skeleton distance and the changed diff lines. Attempts go
to a scratch trial database (~/decomp/runs/probe/probe.sqlite), never a ledger.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/probe/probe.sqlite")
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")


def _db():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    fresh = not TRIAL.exists()
    conn = sqlite3.connect(TRIAL, timeout=600)
    if fresh:
        conn.executescript((ROOT / "kb/schema.sql").read_text())
        conn.execute("ATTACH DATABASE ? AS c", (CAMPAIGN.as_uri() + "?mode=ro",))
        for table in ("extraction", "tus", "functions"):
            conn.execute(f"INSERT INTO main.{table} SELECT * FROM c.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE c")
    return conn


def probe(function: str, source: str, label: str = "probe", conn=None):
    from solver import workspace
    conn = conn or _db()
    ws = workspace.bootstrap(REPO, function)
    a = workspace.score(ws, REPO, f"{function}_probe_{time.time_ns()}", source, conn=conn, func=function,
                        strategy=f"probe:{label}"[:120], run_kind="probe")
    conn.commit()
    return a


def changed(diff: str, context: int = 2) -> str:
    lines = (diff or "").splitlines()
    idx = [k for k, l in enumerate(lines) if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    keep = sorted({j for k in idx for j in range(max(0, k - context), min(len(lines), k + context + 1))})
    out, prev = [], None
    for j in keep:
        if prev is not None and j != prev + 1:
            out.append("  ..")
        out.append(lines[j])
        prev = j
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("function")
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    from solver import site_edits, skeleton, workspace
    conn = _db()
    for f in args.files:
        a = probe(args.function, f.read_text(), f.name, conn)
        print(f"== {f.name}: exact={workspace.repair_complete(a)} compiled={a.compiled} "
              f"gradient={site_edits.gradient(a)} skeleton={skeleton.distance(a.diff or '') if a.compiled else None}")
        if not a.compiled:
            print((a.compiler_stderr or "")[-800:])
        else:
            print(changed(a.diff))


if __name__ == "__main__":
    main()

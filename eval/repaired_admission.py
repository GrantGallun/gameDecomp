"""Admission is not a match. Convert the newly-compiling ones.

The header route plus the repair chain admits ~22% of the drafts that never compiled, and the best so
far (`__osSiRawStartDma`) lands at 93.214. A compiling candidate is scoreable, which is the whole point
-- but a score is not a verdict, and `solver/repair`'s deterministic rungs have never been offered
these sources because they never compiled.

Reconstruction is FREE: `state.json` records the includes that were added and the stages that fired, and
every stage in the route is deterministic and pure with respect to the workspace. So the admitted source
is rebuilt from `base.c` + `add_includes` + `repair_chain` with no compile, then the repair rungs run on
it and the OBJECT decides. Every candidate is logged.

    python3 -m eval.repaired_admission [--limit N] [--functions a,b,c]
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import header_admission as ha                                     # noqa: E402
from solver import workspace                                               # noqa: E402

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"
DEFAULT_STATE = ROOT / "eval/results/header-admission-full-20260917/state.json"


def build_context(conn, repo: Path) -> dict:
    """The expensive per-corpus inputs, built once instead of per function."""
    from solver import buildtypes, typepool, unknowns
    known = buildtypes.type_names(repo)
    corpus = sorted(p.name for p in (repo / "nonmatchings").iterdir() if (p / "base.c").is_file())
    return {"known": known,
            "pool": typepool.pool(conn, typepool.type_uses(repo, corpus), known),
            "symbols": unknowns.symbol_table(repo)}


def admitted_source(conn, repo: Path, name: str, row: dict, ctx: dict):
    """Rebuild the source the admission route produced, without compiling anything."""
    ws = workspace.bootstrap(repo, name)
    path = ws / "base.c"
    if not path.is_file():
        return None, "no-draft", ws
    draft = path.read_text(errors="replace")
    if row.get("baseline_compiled"):
        return draft, "baseline (compiled as drafted)", ws
    includes = row.get("includes") or []
    if includes:
        draft = ha.add_includes(repo, draft, includes)
    from eval import zero_token_harvest as zth
    repaired, stages, _plans, declined = zth.repair_chain(conn, name, draft, ctx["known"],
                                                          ctx["pool"], ctx["symbols"])
    if declined:
        return None, f"repair-chain declined ({declined})", ws
    if repaired == draft and not includes:
        return None, "reconstruction changed nothing", ws
    return repaired, f"includes={includes} stages={stages}", ws


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/repaired-admission-20260917")
    ap.add_argument("--functions", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-score", type=float, default=0.0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    rows_path = args.out / "state.json"
    rows = json.loads(rows_path.read_text()) if rows_path.is_file() else {}

    state = json.loads(args.state.read_text())
    if args.functions:
        picked = [(n.strip(), state.get(n.strip(), {"baseline_compiled": True}))
                  for n in args.functions.split(",") if n.strip()]
    else:
        compiled = [(n, r) for n, r in state.items()
                    if r.get("compiled") and (r.get("score") or 0) >= args.min_score]
        compiled.sort(key=lambda kv: -(kv[1].get("score") or 0))
        picked = compiled[:args.limit] if args.limit else compiled
    print(f"converting {len(picked)} admitted candidates", flush=True)

    conn = sqlite3.connect(str(args.db))
    ctx = build_context(conn, args.repo)
    done = 0
    for name, row_state in picked:
        if name in rows and rows[name].get("status") in {"EXACT", "not-exact"}:
            continue
        row: dict = {"function": name, "admitted_score": row_state.get("score")}
        source, why, ws = admitted_source(conn, args.repo, name, row_state, ctx)
        row["reconstruction"] = why
        if source is None:
            row["status"] = "no-admitted-source"
            rows[name] = row
            print(f"  {name:<34} no-admitted-source ({why})", flush=True)
            continue
        base = workspace.score(ws, args.repo, name, source, conn=conn, func=name,
                               strategy="repaired-admission:admitted", iteration=0,
                               run_kind="repaired-admission")
        row.update(reconstructed_score=base.score, reconstructed_exact=bool(base.exact),
                   compiled=bool(base.compiled))
        if base.exact:
            row["status"] = "EXACT"
            row["label"] = "admitted"
        elif not base.compiled:
            row["status"] = "reconstruction-mismatch"
            row["error"] = (base.compiler_stderr or "").strip().splitlines()[:3]
        else:
            from eval import repair as repair_mod
            from miner import globals_layout
            objs = globals_layout.objects(conn)
            best_label, best_score = "admitted", base.score
            row["status"] = "not-exact"
            for label, candidate in repair_mod.passes(source, conn=conn, func=name, repo=args.repo,
                                                      ws=ws, objs=objs, diff=base.diff or ""):
                if label == "baseline":
                    continue
                att = workspace.score(ws, args.repo, name, candidate, conn=conn, func=name,
                                      strategy=f"repaired-admission:{label}", iteration=0,
                                      run_kind="repaired-admission")
                row.setdefault("tried", {})[label] = att.score
                if att.exact:
                    row.update(status="EXACT", label=label, score=att.score)
                    break
                if att.compiled and (att.score or 0) > (best_score or 0):
                    best_label, best_score = label, att.score
            row.update(best_label=best_label, best_score=best_score)
        rows[name] = row
        done += 1
        rows_path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"  {name:<34} {row['status']:<24} score {row.get('admitted_score')} -> "
              f"{row.get('reconstructed_score')} best={row.get('best_score')} "
              f"{row.get('label') or ''}", flush=True)
    print(collections.Counter(r.get("status") for r in rows.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

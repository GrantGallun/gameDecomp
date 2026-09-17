"""Close compiling-but-not-exact candidates deterministically. No model, no GPU.

Why this exists. Two days of work went into admission -- making functions compile at all -- and the
re-run has produced candidates at 100.00, 98.46, 96.06 and 92.50 similarity that are not byte-exact.
A compiling candidate is not a match, and the pass with the best measured yield on exactly this
population was not on the path: `eval/repair.py` applies `structgen.repad` and `tracefix`, but not
`solver.regalloc_search`.

The 2026-09-15 progress census measured why that matters. Every `regalloc_search` job to date, keyed
by the residual it started from:

    register-dominant, at most 2 other faults    228 jobs   155 exact
    more than 2 other faults                      16 jobs     0 exact

and its own record (`solver/regalloc_search.py:10`) reached 73 of 78 register-only functions and 63
of 145 with at most two other faults. So the lever is: take a compiling candidate, and if its residual
is register-dominant, search it.

`enable=True` is on by default here. Enabling roots are edits that leave the gradient unchanged but
let another family fire, so they never win a beam slot; in an offline test they made 15 of a
30-function family exact, none of which was reachable before.

Usage:
    python3 -m eval.close_nearmiss --db ... --repo ... --out ... [--functions a,b] [--budget N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import signals, workspace                                # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def best_compiled_source(conn, name: str) -> tuple[int, str, float] | None:
    """The highest-scoring COMPILING attempt for a function, with its receipt id."""
    row = conn.execute(
        "select a.id, a.source_code, a.score from attempts a join functions f on f.addr = a.func_addr "
        "where f.name = ? and a.compiled = 1 and a.source_code is not null "
        "order by a.score desc limit 1", (name,)).fetchone()
    return (int(row[0]), row[1], float(row[2] or 0.0)) if row else None


def profile_of(diff: str, score: float, exact: bool, compiled: bool) -> dict[str, int]:
    verdict = signals.analyse(diff or "", score, exact, compiled)
    return {axis: int(getattr(verdict, axis)) for axis in AXES}


def fault_total(compiled) -> int:
    """Total classified faults from a candidate's diff. Lower is better.

    Used only to pick which layout candidate to carry forward. It is NOT the acceptance oracle --
    exactness is, and always was; `regalloc_search`'s own ranking is a separate gradient.
    """
    if not compiled.compiled:
        return 10 ** 6
    return sum(profile_of(compiled.diff, 0.0, compiled.exact, True).values())


def close_one(conn, repo: Path, name: str, *, budget: int, beam: int, depth: int,
              enable: bool, trace: bool = True, layout: bool = True,
              use_globals: bool = False, force_regalloc: bool = False) -> dict:
    """Run the register-allocation search from the function's best compiling candidate."""
    from solver import regalloc_search

    row: dict = {"function": name, "status": "ok"}
    found = best_compiled_source(conn, name)
    if not found:
        row["status"] = "no-compiling-candidate"
        return row
    receipt, source, score = found
    row.update(source_attempt_id=receipt, start_score=score)

    ws = workspace.bootstrap(repo, name)
    target_dump = (ws / "target_object_dump_normalized.s").read_text(errors="replace")
    dump_path = ws / f"{name}_object_dump_normalized.s"

    def compile_candidate(candidate: str, label: str) -> "regalloc_search.Compiled":
        att = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                              strategy=f"close-nearmiss:{label}", iteration=0,
                              run_kind="close-nearmiss")
        dump = None
        if att.compiled and dump_path.exists():
            dump = dump_path.read_text(errors="replace")
        return regalloc_search.Compiled(bool(att.compiled), bool(att.exact), dump, att.diff or "")

    # The baseline is compiled and profiled first, because the census result is conditional on the
    # residual's SHAPE: register-dominant with at most two other faults is the band this pass closes,
    # and reporting a search that was out of band as a failure would be the wrong conclusion.
    base = compile_candidate(source, "baseline")
    row["baseline_compiled"] = base.compiled
    row["baseline_exact"] = base.exact
    profile = profile_of(base.diff, score, base.exact, base.compiled)
    row["profile"] = profile
    dominant = max(profile, key=lambda axis: profile[axis])
    row["dominant"] = dominant
    others = sum(v for k, v in profile.items() if k != dominant)
    row["other_faults"] = others
    if trace:
        print(f"  {name}: score={score:.2f} dominant={dominant} other_faults={others} "
              f"profile={profile}", flush=True)
    if base.exact:
        row["status"] = "already-exact"
        return row

    # LAYOUT FIRST, because the census result is conditional on the residual's SHAPE. The 2026-09-15
    # progress census: register-dominant with at most two other faults closed 155 of 228; more than
    # two closed 0 of 16. Its own conclusion was "the way to more exacts is to cut non-register faults
    # until a node enters that band". The first candidate tried here,
    # decrementRaceChallengeTimeLimit at 96.06, was dominant=layout with 14 layout faults against 4
    # register ones -- squarely out of band, and recording that as a regalloc failure would be the
    # wrong conclusion. `eval.repair` owns the layout passes; run them, keep the best, then search.
    layout_best_label, layout_source, layout_base = "baseline", source, base
    if layout:
        try:
            from eval import repair as repair_mod
            from miner import globals_layout
            objs = globals_layout.objects(conn) if use_globals else None
            for label, candidate in repair_mod.passes(source, conn=conn, func=name, repo=repo,
                                                      ws=ws, objs=objs):
                compiled = compile_candidate(candidate, f"layout:{label}")
                row.setdefault("layout_attempts", []).append(
                    {"label": label, "compiled": compiled.compiled, "exact": compiled.exact})
                if compiled.exact:
                    row.update(exact=True, exact_source=candidate, best_label=f"layout:{label}",
                               status="exact", closed_by="layout")
                    return row
                if compiled.compiled and fault_total(compiled) < fault_total(layout_base):
                    layout_best_label, layout_source, layout_base = f"layout:{label}", candidate, compiled
        except Exception as exc:                                    # noqa: BLE001
            row["layout_error"] = f"{type(exc).__name__}: {exc}"
        if layout_best_label != "baseline":
            row["layout_best"] = layout_best_label
            row["layout_profile"] = profile_of(layout_base.diff, 0.0, layout_base.exact,
                                               layout_base.compiled)
            if trace:
                print(f"    layout improved: {layout_best_label} {row['layout_profile']}", flush=True)

    source, base = layout_source, layout_base
    after = profile_of(base.diff, 0.0, base.exact, base.compiled)
    dominant = max(after, key=lambda axis: after[axis])
    row["dominant_after_layout"] = dominant
    row["other_faults_after_layout"] = sum(v for k, v in after.items() if k != dominant)
    if dominant != "regalloc" and not force_regalloc:
        row["status"] = "out-of-band"
        row["note"] = ("dominant residual after layout repair is not register allocation, so "
                       "regalloc_search is the wrong instrument for this shape")
        return row

    t0 = time.time()
    try:
        outcome = regalloc_search.search(
            name, source, compile_candidate, target_dump,
            budget=budget, beam=beam, depth=depth, baseline=base, enable=enable)
    except Exception as exc:                                        # noqa: BLE001
        row["status"] = "search-raised"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row["seconds"] = round(time.time() - t0, 2)
    row["compiles"] = outcome.compiles
    row["exact"] = bool(outcome.exact)
    row["best_label"] = outcome.best_label
    row["baseline_gradient"] = list(outcome.baseline_gradient or ())
    row["best_gradient"] = list(outcome.best_gradient or ())
    if outcome.exact:
        row["exact_source"] = outcome.best_source
        row["status"] = "exact"
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=Path(DEFAULT_REPO))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--functions", default="",
                    help="comma-separated names; default is every compiling non-exact function "
                         "with at least one logged attempt")
    ap.add_argument("--budget", type=int, default=300)
    ap.add_argument("--beam", type=int, default=3)
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--no-enable", action="store_true")
    ap.add_argument("--no-layout", action="store_true",
                    help="skip the layout prepass. It is ON by default: a residual whose dominant "
                         "fault is not register allocation is out of regalloc_search's measured band, "
                         "and the census's own conclusion was to cut those faults first")
    ap.add_argument("--globals", action="store_true",
                    help="also feed global object layouts to repad (eval.repair's --globals)")
    ap.add_argument("--force-regalloc", action="store_true",
                    help="search even when the residual is out of band; recorded separately so an "
                         "out-of-band result is never reported as the pass failing")
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db, timeout=120)
    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        names = [r[0] for r in conn.execute(
            "select f.name from functions f where exists "
            "(select 1 from attempts a where a.func_addr = f.addr and a.compiled = 1) "
            "and not exists (select 1 from attempts a where a.func_addr = f.addr and a.exact = 1) "
            "order by (select max(a.score) from attempts a where a.func_addr = f.addr "
            "and a.compiled = 1) desc")]
    print(f"candidates: {len(names)}", flush=True)

    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print(f"BUDGET: {args.seconds}s reached", flush=True)
            break
        row = close_one(conn, args.repo, name, budget=args.budget, beam=args.beam,
                        depth=args.depth, enable=not args.no_enable,
                        layout=not args.no_layout, use_globals=args.globals,
                        force_regalloc=args.force_regalloc)
        state[name] = row
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{len(state)}/{len(names)}] {name:<46} exact={row.get('exact')} "
              f"compiles={row.get('compiles')} {row.get('status')}", flush=True)

    exact = sorted(n for n, r in state.items() if r.get("exact"))
    summary = {
        "population": len(state),
        "exact": len(exact),
        "exact_functions": exact,
        "in_band_register_dominant": sum(
            1 for r in state.values() if r.get("dominant") == "regalloc"
            and (r.get("other_faults") or 0) <= 2),
        "status_counts": {s: sum(1 for r in state.values() if r.get("status") == s)
                          for s in sorted({r.get("status") for r in state.values()})},
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

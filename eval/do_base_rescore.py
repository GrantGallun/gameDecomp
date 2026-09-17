"""Score the m2c draft of every do-bearing function the ban kept from ever being attempted.

The 168 do-bearing functions with no attempt in the knowledge base are not functions with no
candidate. `tools/claude --bootstrap-only` writes `base.c` in each workspace, and that file is an
**m2c decompilation of the target assembly** -- binary-derived, labelled as such in its own header,
and, characteristically for m2c, written with `do { ... } while (...)`. The ban refused it before IDO
was invoked, so no attempt was ever recorded and nothing counted the function as a near miss. That is
why they read as "never attempted": the pipeline had already declined them.

With the ban gone the draft can simply be compiled. This does that and reports what the oracle says;
`--repair` additionally runs the deterministic repair rungs on anything that compiles but is not exact.

    python3 -m eval.do_base_rescore --out eval/results/do-base-<date> [--survey] [--repair] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import signals, workspace                                    # noqa: E402

DEFAULT_DB = "/home/grant/decomp/kb-sbk1.sqlite"
DEFAULT_REPO = Path("/home/grant/decomp/sbk1")
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def population(path: Path, repo: Path, kind: str) -> list[str]:
    if not path.is_file():
        import eval.do_while_population as dwp
        dwp.main(["--out", str(path)])
    names = json.loads(path.read_text())[kind]
    return [n for n in names if (repo / "nonmatchings" / n / "base.c").is_file()]


def profile_of(att) -> dict:
    if not att.compiled:
        return {}
    verdict = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), True)
    return {axis: int(getattr(verdict, axis)) for axis in AXES}


def run_one(conn, repo: Path, name: str, *, repair: bool, admit: bool = True,
            context: dict | None = None) -> dict:
    row: dict = {"function": name}
    ws = workspace.bootstrap(repo, name)
    draft_path = ws / "base.c"
    if not draft_path.is_file():
        return dict(row, status="no-base-draft")
    draft = draft_path.read_text(errors="replace")
    row["uses_do"] = "do {" in draft or "\tdo {" in draft
    att = workspace.score(ws, repo, name, draft, conn=conn, func=name,
                          strategy="do-base-rescore:m2c-draft", iteration=0,
                          run_kind="do-base-rescore")
    row.update(compiled=bool(att.compiled), exact=bool(att.exact), score=att.score,
               profile=profile_of(att))
    if att.exact:
        row["status"] = "exact"
        row["exact_source"] = draft
        return row
    if not att.compiled:
        row["status"] = "not-compiling"
        row["error"] = (att.compiler_stderr or "").strip().splitlines()[-3:]
        # ADMISSION FIRST. A draft that does not build cannot be scored, and `repair.passes` can only
        # rewrite a candidate the front end accepted. `repair_context.normalize` is the project's
        # deterministic admission path keyed on the compiler's own error: C89 spellings, the
        # void-pointer byte-arithmetic families, bitcasts. Measured on this population: 163 of 166
        # never-attempted drafts fail here, 93 with `Syntax Error` and 53 with `Empty declaration
        # specifiers`, which is an admission wall and not the `do` wall.
        if not admit:
            return row
        # FIRST, the m2c placeholder type. Observed terminal error on the drafts that survive every
        # other stage: `? sp20;` -- m2c's unknown type, which IDO reports as `Empty declaration
        # specifiers` (53 of the 163 failures). `solver.placeholder_declarations` is the pass for it
        # (`UNKNOWN = r"(?:\?|M2C_UNK)"`, `LOCAL = ... {UNKNOWN} <stars> <name> ;`) and nothing in the
        # recovery path calls it, so every later stage is being asked to fix a draft that cannot parse.
        placed = None
        try:
            from solver import placeholder_declarations as pd_mod
            headers = pd_mod.header_names(repo, draft)
            rows, report = pd_mod.propose(draft, name, headers)
            row["placeholder_report"] = {k: v for k, v in (report or {}).items()
                                         if isinstance(v, (int, str, bool, list))}
            if rows:
                placed = rows
        except Exception as exc:                                         # noqa: BLE001
            row["placeholder_error"] = f"{type(exc).__name__}: {exc}"
        if placed:
            for label, candidate in placed:
                fixed = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                                        strategy=f"do-base-rescore:placeholder:{label}", iteration=0,
                                        run_kind="do-base-rescore", parent_attempt_id=att.receipt_id,
                                        relation="do-base-rescore")
                if fixed.exact:
                    row.update(compiled=True, exact=True, status="exact", score=fixed.score,
                               admitted_by=f"placeholder:{label}", exact_source=candidate, error=None)
                    return row
                if fixed.compiled:
                    row.update(compiled=True, status="compiled", score=fixed.score,
                               profile=profile_of(fixed), admitted_by=f"placeholder:{label}",
                               error=None, admitted_source=candidate)
                    return row
            row["placeholder_compiled"] = False
        # `zero_token_harvest.repair_chain` is the applier for the classes that dominate this
        # population. `compilefix` maps `Syntax Error`/`Empty declaration specifiers` to `typedecl` and
        # `byte-index`, and this is the only caller that runs them: `memberaccess.rewrite` (byte-index),
        # `typedecl.synthesize`, then `globaldecl.declare`. It is pure -- no workspace, no compile -- so
        # it is tried before the expensive recovery stages.
        admitted = None
        try:
            from eval import zero_token_harvest as zth
            from solver import buildtypes
            ctx = context or {}
            known = ctx.get("known_types") or buildtypes.type_names(repo)
            # THE POOL IS THE WHOLE POINT for a local-only type. `typedecl.plan` gives a local no
            # per-function evidence key, so `PlayerCommandState *var_s0;` in MusStartEffect can only be
            # declared from the cross-function pool -- and with `pool=None` the planner hits
            # `if not fields: continue` and returns no plans, which is exactly what the round-5 probe
            # measured (`plans=0`). Built once in `main` over every bootstrapped draft, as
            # `zero_token_harvest.main` does; rebuilding it per function would be 2,022 drafts per call.
            repaired, stages, plans, declined = zth.repair_chain(
                conn, name, draft, known, ctx.get("pool"), ctx.get("symbols"))
            row["harvest_stages"] = stages
            row["harvest_plans"] = len(plans or ())
            if declined:
                row["harvest_declined"] = declined
            if repaired != draft:
                admitted = [(f"harvest:{'+'.join(stages) or 'none'}", repaired)]
        except Exception as exc:                                         # noqa: BLE001
            row["harvest_error"] = f"{type(exc).__name__}: {exc}"
        if admitted:
            for label, candidate in admitted:
                fixed = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                                        strategy=f"do-base-rescore:{label}", iteration=0,
                                        run_kind="do-base-rescore", parent_attempt_id=att.receipt_id,
                                        relation="do-base-rescore")
                if fixed.exact:
                    row.update(compiled=True, exact=True, status="exact", score=fixed.score,
                               admitted_by=label, exact_source=candidate, error=None)
                    return row
                if fixed.compiled:
                    row.update(compiled=True, status="compiled", score=fixed.score,
                               profile=profile_of(fixed), admitted_by=label, error=None)
                    row["admitted_source"] = candidate
                    draft, att = candidate, fixed
                    break
            else:
                row["harvest_compiled"] = False
        if row.get("compiled"):
            row["status"] = "compiled"
            return row
        # `repair_context.normalize` is NOT the admission applier: it covers only C89 spellings and the
        # void-pointer byte-arithmetic families, and it returns NOTHING for
        # `Syntax Error/contradicted-primitive-pointer` (drawCourseRecordBanner) or only a `c89` variant
        # for `Syntax Error/undeclared-param-type` (drawCharacterSelectCourseListOptions) -- the two
        # classes that dominate this population. `solver.compile_recovery.variants` is the wider net.
        try:
            from solver import compile_recovery
            rows, reports = compile_recovery.variants(conn, repo, name, ws, draft, att)
            row["recovery_stages"] = [r.get("stage") for r in reports if isinstance(r, dict)]
            admitted = rows
        except Exception as exc:                                         # noqa: BLE001
            row["recovery_error"] = f"{type(exc).__name__}: {exc}"
            admitted = None
        if not admitted:
            from solver import repair_context
            admitted = list(repair_context.normalize(draft, att.compiler_stderr or "", name))
            row["admitted_via"] = "repair_context.normalize"
        else:
            row["admitted_via"] = "compile_recovery.variants"
        for label, candidate in admitted:
            fixed = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                                    strategy=f"do-base-rescore:{label}", iteration=0,
                                    run_kind="do-base-rescore", parent_attempt_id=att.receipt_id,
                                    relation="do-base-rescore")
            if not fixed.compiled:
                continue
            row.update(compiled=True, score=fixed.score, profile=profile_of(fixed),
                       admitted_by=label, status="compiled",
                       error=None)
            if fixed.exact:
                row.update(exact=True, status="exact", exact_source=candidate)
                return row
            draft, att = candidate, fixed
            break
        else:
            row["admission"] = "no candidate compiled"
            return row
    row["admitted_source"] = draft
    row["status"] = "compiled"
    if not repair:
        return row
    best_label, best_score, best_source = "m2c-draft", att.score, draft
    try:
        from eval import repair as repair_mod
        from miner import globals_layout
        objs = globals_layout.objects(conn)
        for label, candidate in repair_mod.passes(draft, conn=conn, func=name, repo=repo, ws=ws,
                                                 objs=objs, diff=att.diff or ""):
            if label == "baseline":
                continue
            fixed = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                                   strategy=f"do-base-rescore:{label}", iteration=0,
                                   run_kind="do-base-rescore", parent_attempt_id=att.receipt_id,
                                   relation="do-base-rescore")
            if fixed.exact:
                row.update(exact=True, status="exact", best_label=label, exact_source=candidate,
                           score=fixed.score)
                return row
            if fixed.compiled and (fixed.score or 0) > (best_score or 0):
                best_label, best_score, best_source = label, fixed.score, candidate
    except Exception as exc:                                             # noqa: BLE001
        row["repair_error"] = f"{type(exc).__name__}: {exc}"
    row.update(best_label=best_label, best_score=best_score, best_source=best_source)
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/do-base-rescore-20260917")
    ap.add_argument("--population", type=Path,
                    default=ROOT / "eval/results/do-while-population-20260917.json")
    ap.add_argument("--kind", default="never", choices=("never", "live"))
    ap.add_argument("--functions", default="")
    ap.add_argument("--survey", action="store_true", help="report draft availability and stop")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--no-pool", action="store_true",
                    help="skip the cross-function type pool. It is ON by default: a locale-only type "
                         "such as PlayerCommandState has no per-function evidence key, so without the "
                         "pool `typedecl.plan` returns no plans at all and the admission pass cannot "
                         "fire (measured round 5)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args(argv)

    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        names = population(args.population, args.repo, args.kind)
    if args.survey:
        repo_ws = args.repo / "nonmatchings"
        all_names = json.loads(args.population.read_text())[args.kind] if args.population.is_file() else []
        have_draft = sum(1 for n in all_names if (repo_ws / n / "base.c").is_file())
        have_ws = sum(1 for n in all_names if (repo_ws / n / "target.s").is_file())
        print(f"kind={args.kind}: {len(all_names)} functions")
        print(f"  workspace with target.s : {have_ws}")
        print(f"  workspace with base.c   : {have_draft}")
        print(f"  neither                 : {len(all_names) - have_draft}")
        return 0
    if args.limit:
        names = names[:args.limit]
    print(f"population: {len(names)} do-bearing functions with an m2c draft", flush=True)

    args.out.mkdir(parents=True, exist_ok=True)
    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    conn = sqlite3.connect(args.db, timeout=120)
    context: dict = {}
    if not args.no_pool:
        # Same construction as `eval/zero_token_harvest.main`: the corpus is EVERY bootstrapped draft,
        # not this run's selection -- a type's layout is only as complete as the functions that
        # contributed to it.
        from eval import zero_token_harvest as zth
        from solver import buildtypes, typepool, unknowns
        known_types = buildtypes.type_names(args.repo)
        corpus = sorted(p.name for p in (args.repo / "nonmatchings").iterdir()
                        if (p / "base.c").is_file())
        context = {"known_types": known_types,
                   "pool": typepool.pool(conn, typepool.type_uses(args.repo, corpus), known_types),
                   "symbols": unknowns.symbol_table(args.repo)}
        print(f"context: {len(known_types)} known types, {len(context['pool'])} pooled types "
              f"from {len(corpus)} drafts", flush=True)
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print("BUDGET reached", flush=True)
            break
        try:
            state[name] = run_one(conn, args.repo, name, repair=args.repair, context=context)
        except Exception as exc:                                         # noqa: BLE001
            state[name] = {"function": name, "status": "raised",
                           "error": f"{type(exc).__name__}: {exc}"}
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        row = state[name]
        print(f"[{len(state)}/{len(names)}] {name:<46} do={row.get('uses_do')} "
              f"compiled={row.get('compiled')} score={row.get('score')} exact={row.get('exact')} "
              f"{row.get('status')} {row.get('best_label', '')}", flush=True)

    exact = sorted(n for n, r in state.items() if r.get("exact"))
    summary = {"kind": args.kind, "population": len(names), "recorded": len(state),
               "uses_do": sum(1 for r in state.values() if r.get("uses_do")),
               "compiled": sum(1 for r in state.values() if r.get("compiled")),
               "exact": len(exact), "exact_functions": exact,
               "status_counts": dict(Counter(r.get("status") for r in state.values())),
               "mean_compiling_score": round(
                   sum(r["score"] for r in state.values() if r.get("compiled")) /
                   max(1, sum(1 for r in state.values() if r.get("compiled"))), 3)}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "exact_functions"}, indent=2))
    print("exact:", ", ".join(exact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

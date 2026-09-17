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


def run_one(conn, repo: Path, name: str, *, repair: bool, admit: bool = True) -> dict:
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
        from solver import repair_context
        for label, candidate in repair_context.normalize(draft, att.compiler_stderr or "", name):
            fixed = workspace.score(ws, repo, name, candidate, conn=conn, func=name,
                                    strategy=f"do-base-rescore:normalize:{label}", iteration=0,
                                    run_kind="do-base-rescore")
            if not fixed.compiled:
                continue
            row.update(compiled=True, score=fixed.score, profile=profile_of(fixed),
                       admitted_by=label, status="compiled" if not fixed.exact else "exact",
                       draft=att, source=candidate)
            if fixed.exact:
                row["exact"] = True
                row["exact_source"] = candidate
                return row
            draft, att = candidate, fixed
            break
        else:
            return row
    row.setdefault("draft", att)
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
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print("BUDGET reached", flush=True)
            break
        try:
            state[name] = run_one(conn, args.repo, name, repair=args.repair)
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

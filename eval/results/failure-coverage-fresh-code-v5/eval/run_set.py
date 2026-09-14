"""Run the solver over a frozen evaluation set and report by stratum.

The aggregate number hides what matters. 75% on small leaf functions and 5% on
large non-leaf ones averages to something respectable and describes nothing, so
results are always broken out by tier and leaf/non-leaf.

Results are written incrementally. A long run over large functions can take
hours, and WSL on this machine has died mid-run more than once -- losing a
completed function to a crash is pure waste when appending a line prevents it.

Run:
    python3 -m eval.run_set --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \\
        --set eval/sets/sbk1_v1.json --split dev --model gpt-oss:20b -n 4
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

from eval import experiment
from kb import provenance
from solver import llm, pipeline, refine, shaped_flywheel, siblings


def load_done(path: Path) -> dict:
    if not path.exists():
        return {}
    done = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            done[row["function"]] = row
        except json.JSONDecodeError:
            continue
    return done


def report(rows: list[dict]) -> None:
    # A function that never reached a model call is an INFRASTRUCTURE failure,
    # not a model failure. Scoring a bootstrap timeout as 0% conflates "the
    # solver could not do it" with "the harness never asked", which understates
    # the rate and pollutes the mean. Reported separately, never averaged in.
    never_ran = [r for r in rows if r.get("draws", 0) == 0]
    rows = [r for r in rows if r.get("draws", 0) > 0]
    if never_ran:
        print(f"\nEXCLUDED -- never reached a model call ({len(never_ran)}):")
        for r in never_ran:
            print(f"  {r['function'][:40]:42} {r.get('error','no draws')[:50]}")

    if not rows:
        print("no attempted functions to report")
        return

    by = defaultdict(list)
    for r in rows:
        by[(r["tier"], "leaf" if r["leaf"] else "non-leaf")].append(r)

    print("\n" + "=" * 72)
    print("RESULTS BY STRATUM")
    print("=" * 72)
    print(f"{'tier':8} {'kind':9} {'n':>3} {'exact':>7} {'rate':>7} {'mean best':>10}")
    print("-" * 72)

    order = {"tiny": 0, "small": 1, "medium": 2, "large": 3, "huge": 4}
    for key in sorted(by, key=lambda k: (order.get(k[0], 9), k[1])):
        rs = by[key]
        exact = sum(1 for r in rs if r["exact"])
        mean = sum(r["best_score"] for r in rs) / len(rs)
        print(f"{key[0]:8} {key[1]:9} {len(rs):3} {exact:7} "
              f"{100.0*exact/len(rs):6.1f}% {mean:9.2f}%")

    print("-" * 72)
    exact = sum(1 for r in rows if r["exact"])
    leaf = [r for r in rows if r["leaf"]]
    nonleaf = [r for r in rows if not r["leaf"]]
    print(f"{'TOTAL':8} {'':9} {len(rows):3} {exact:7} "
          f"{100.0*exact/len(rows) if rows else 0:6.1f}% "
          f"{sum(r['best_score'] for r in rows)/len(rows) if rows else 0:9.2f}%")
    for label, subset in (("leaf", leaf), ("non-leaf", nonleaf)):
        if subset:
            e = sum(1 for r in subset if r["exact"])
            print(f"  {label:10} {len(subset):3} {e:7} {100.0*e/len(subset):6.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--set", required=True, type=Path)
    ap.add_argument("--split", choices=["dev", "heldout"], default="dev")
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("-n", "--samples", type=int, default=4)
    ap.add_argument("--temp", type=float, default=0.7)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--think", default="low")
    ap.add_argument("--num-thread", type=int, default=12)
    ap.add_argument("--pace", type=float, default=1.0)
    ap.add_argument("--historical-siblings", action="store_true",
                    help="restrict siblings to functions matched BEFORE "
                         "the target, as a human would have had")
    ap.add_argument("--pipeline", action="store_true",
                    help="use the triage pipeline (GATHER/SAMPLE/TRIAGE)")
    ap.add_argument("--siblings", action="store_true",
                    help="allow matched-sibling context in lower bands")
    ap.add_argument("--frozen-sibling-pool", type=Path,
                    help="use this immutable verified-source bundle instead "
                         "of reloading a growing pool from the live database")
    ap.add_argument("--shaped-sibling-pool", type=Path,
                    help="use a typed verified-function graph; weak matches get "
                         "invariants while strong matches may get exact source")
    ap.add_argument("--permute-seconds", type=int, default=120)
    ap.add_argument("--model-repair-draws", type=int, default=0,
                    help="structured edit proposals per parent (pipeline only)")
    ap.add_argument("--model-repair-depth", type=int, default=2)
    ap.add_argument("--model-repair-beam", type=int, default=4)
    ap.add_argument("--kb-context", action="store_true",
                    help="inject verified KB facts into the prompt")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    sets = json.loads(args.set.read_text())
    entries = sets[args.split]

    # A KB fed the ground-truth oracle measures a ceiling, not a capability.
    # Read it before naming the output file so a ceiling run can never land in
    # the same file as a clean one.
    # Attempts are written throughout long model/compile runs, and helper
    # stages may briefly overlap those writes.  Match the other heavy eval
    # harnesses: wait for the writer instead of turning lock contention into a
    # zero-draw model result.
    taint_conn = sqlite3.connect(args.db.expanduser(), timeout=120)
    taint_conn.execute("PRAGMA busy_timeout = 120000")
    kb_taint = provenance.digest(taint_conn)
    ceiling = bool(kb_taint)
    if ceiling:
        print(provenance.banner(taint_conn), "\n", flush=True)
    taint_conn.close()

    model_repair_tag = (f"_mr{args.model_repair_draws}"
                        f"d{args.model_repair_depth}b{args.model_repair_beam}"
                        if args.model_repair_draws else "")
    out = args.out or args.set.with_name(
        f"{args.set.stem}_{args.split}_{args.model.replace(':', '-')}"
        f"{'_pipe' if args.pipeline else ''}"
        f"{'_sibhist' if args.historical_siblings else '_sibshape' if args.shaped_sibling_pool else '_sib' if args.siblings else ''}"
        f"{model_repair_tag}"
        f"{'_kb' if args.kb_context else ''}"
        f"{'_ceiling' if ceiling else ''}.jsonl")

    if args.split == "heldout" and ceiling:
        print("refusing: heldout on a tainted KB. The held-out set exists to\n"
              "produce one honest number, and a KB holding the reference\n"
              "decomp's own types cannot produce one. Ceiling runs are dev-only.",
              file=sys.stderr)
        raise SystemExit(2)

    if args.split == "heldout":
        print("*** HELD-OUT SPLIT ***")
        print("Report this number as-is. Do not tune prompts and re-run;")
        print("that converts the held-out set into a development set.\n")

    repo = args.repo.expanduser()
    conn = sqlite3.connect(args.db.expanduser(), timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    endpoint = llm.host()

    # Refuse to blend implementations. Over one evening the sampling budget,
    # the prompt hints, the routing and num_ctx all changed; resuming an older
    # results file with newer code yields a number describing no system that
    # ever existed.
    if args.frozen_sibling_pool and args.shaped_sibling_pool:
        ap.error("--frozen-sibling-pool and --shaped-sibling-pool are mutually exclusive")
    if (args.frozen_sibling_pool or args.shaped_sibling_pool) \
            and args.historical_siblings:
        ap.error("frozen sibling pools cannot be combined with --historical-siblings")
    if (args.frozen_sibling_pool or args.shaped_sibling_pool) and not args.siblings:
        ap.error("frozen sibling pools require --siblings")
    if args.shaped_sibling_pool and not args.pipeline:
        ap.error("--shaped-sibling-pool requires --pipeline")
    if args.model_repair_draws and not args.pipeline:
        ap.error("--model-repair-draws requires --pipeline")
    frozen_sibling_sources = None
    shaped_sibling_library = None
    if args.frozen_sibling_pool:
        frozen_sibling_sources = siblings.load_source_bundle(
            args.frozen_sibling_pool.expanduser())
    if args.shaped_sibling_pool:
        shaped_sibling_library = shaped_flywheel.load_library(
            args.shaped_sibling_pool.expanduser())
        if args.split == "heldout" and shaped_sibling_library.get("outcomes"):
            ap.error("heldout runs refuse shaped pools carrying DEV retrieval outcomes")

    use_siblings = args.siblings or args.historical_siblings
    sibling_pool = ""
    if use_siblings:
        sibling_pool = ("reference-history" if args.historical_siblings else
                        "shaped:" + str(shaped_sibling_library["digest"])
                        if shaped_sibling_library is not None else
                        siblings.source_digest(frozen_sibling_sources)
                        if frozen_sibling_sources is not None else
                        siblings.source_digest(siblings.verified_sources(conn)))
    fp = experiment.build(args.set, args.split, args.model, args.samples,
                          args.temp, args.think, args.pipeline, use_siblings,
                          args.permute_seconds, kb_taint, sibling_pool,
                          args.model_repair_draws, args.model_repair_depth,
                          args.model_repair_beam)
    may_resume, msg = experiment.check_or_claim(out, fp)
    print(msg, flush=True)
    if not may_resume:
        raise SystemExit(2)
    experiment.record(conn, fp, out)

    done = load_done(out)
    todo = [e for e in entries if e["function"] not in done]
    print(f"set {args.set.name} [{args.split}]: {len(entries)} functions, "
          f"{len(done)} already done, {len(todo)} to run")
    print(f"model {args.model}, best-of-{args.samples} @ temp {args.temp}\n", flush=True)
    if args.model_repair_draws:
        print(f"model repair: {args.model_repair_draws} proposals/parent, "
              f"depth {args.model_repair_depth}, beam {args.model_repair_beam}\n",
              flush=True)

    t_start = time.time()
    for i, entry in enumerate(todo, 1):
        func = entry["function"]
        print(f"[{i}/{len(todo)}] {func} ({entry['tier']}, "
              f"{'leaf' if entry['leaf'] else 'non-leaf'})", flush=True)
        try:
            if args.pipeline:
                r = pipeline.solve(repo, conn, func, args.model, endpoint,
                                   args.samples, args.permute_seconds,
                                   use_siblings, args.timeout, args.think,
                                   args.num_thread,
                                   historical_siblings=args.historical_siblings,
                                   sibling_sources=frozen_sibling_sources,
                                   sibling_library=shaped_sibling_library,
                                   model_repair_draws=args.model_repair_draws,
                                   model_repair_depth=args.model_repair_depth,
                                   model_repair_beam=args.model_repair_beam)
                row = {**entry, "exact": r.exact, "best_score": r.best_score,
                       "draws": r.generations, "stages": len(r.stages), "tokens": r.tokens,
                       "wall_s": round(r.wall_s, 1), "route": r.route,
                       "verdict": r.verdict}
            else:
                traj = refine.sample_one(repo, conn, func, args.model, endpoint,
                                         args.samples, args.timeout, args.think,
                                         args.num_thread, args.pace, args.temp,
                                         verbose=True, use_kb=args.kb_context)
                row = {**entry, "exact": traj.exact, "best_score": traj.best_score,
                       "draws": traj.iterations, "tokens": traj.tokens,
                       "wall_s": round(traj.wall_s, 1)}
        except Exception as exc:
            print(f"    ERROR: {exc}", flush=True)
            row = {**entry, "exact": False, "best_score": 0.0, "draws": 0,
                   "tokens": 0, "wall_s": 0.0, "error": str(exc)[:200]}

        with out.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        done[func] = row

        verdict = "EXACT" if row["exact"] else f"best {row['best_score']:.2f}%"
        print(f"  -> {verdict}  ({row['draws']} draws, {row['wall_s']}s)\n", flush=True)

    rows = [done[e["function"]] for e in entries if e["function"] in done]
    report(rows)
    print(f"\nwall clock: {(time.time()-t_start)/60:.1f} min")
    print(f"results: {out}")


if __name__ == "__main__":
    main()

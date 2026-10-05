"""Collect verified source repairs from the served M0 checkpoint, within a hard budget.

WHY FRESH DATA AT ALL
---------------------
The knowledge base holds 265 improving compiled edges across 26 functions, and 129 of those
are one function. Training on it would satisfy a per-function cap only by discarding most of
the supply, and the two largest contributors are `.rodata`-heavy and library-adjacent. So the
dataset this experiment trains on is generated here, under three rules that make it usable:

  1. THE GENERATOR IS M0. Every target completion comes from the checkpoint the adapter is
     trained on, so the repair distribution at training time matches the one at evaluation
     time. Data from a different model would make the comparison a cross-model comparison.
  2. EVERY LABEL IS COMPILER-VERIFIED. A candidate is a target only if the project's own
     oracle scored it, and it is an IMPROVEMENT only if it beat the candidate it repairs.
  3. EVERY CALL HAS A RECEIPT. Prompt, raw response, model digest, sampling, token cost, wall
     time, extraction status and stop reason are written to a scratch attempt DB, so a
     rejected proposal can be re-examined and a resumed run cannot re-pay for a call.

TWO ARMS PER FUNCTION, AND THEY ARE NOT THE SAME QUESTION
--------------------------------------------------------
  BEST-OF-N (round 0): N independent draws from the leaf prompt. These are the CONTROL.
  REPAIR   (round 1+): the project's repair prompt, carrying the best compiled candidate's C
                       and its own compiler outcome, recorded with that attempt's receipt as
                       the parent.

`Factory` already enforces that distinction. This driver's job is the budget, the
checkpointing, and the scratch database.

BUDGETS ARE ENFORCED, NOT ASPIRED TO
------------------------------------
`--max-calls` and `--max-seconds` bind whichever comes first, are checked before every model
call and inside each function, and refusals and errors consume them. A run that hits either
stops with a receipt saying which one bound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def scratch_db(path: Path, template: Path) -> sqlite3.Connection:
    """A scratch attempt DB with the real schema, so receipts land in the real shape.

    Copied from the research KB's SCHEMA ONLY, never its rows: the history stays read-only and
    the experiment's rows stay separable from it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        src = sqlite3.connect(f"file:{template}?mode=ro", uri=True)
        rows = src.execute(
            "select type, name, sql from sqlite_master where sql is not null").fetchall()
        dst = sqlite3.connect(path)
        for kind, name, sql in rows:
            if name.startswith("sqlite_"):
                continue
            try:
                dst.execute(sql)
            except sqlite3.OperationalError:
                pass
        dst.commit()
        src.close()
    conn = sqlite3.connect(path)
    from kb import attempts as attempt_receipts
    attempt_receipts.ensure_lineage_schema(conn)
    return conn


def collection_pool(spec, sealed: set[str], kb: Path, *, limit: int, min_attempts: int,
                    exclude: set[str]) -> list[dict]:
    """Training-pool work items: unsolved, unsealed, with a real compiler residual to repair.

    Built from `trajectory_factory.candidates`, with one trap avoided: `candidates` SLICES its
    result to `limit`, so `limit=0` returns nothing rather than everything. Passing 0 here cost
    a round trip and looked exactly like "no eligible functions", which is why the pool size is
    asserted non-zero below rather than trusted.
    """
    from eval import trajectory_factory as tf
    items = tf.candidates(spec, limit=10_000, min_attempts=min_attempts,
                          sealed=sealed | exclude, objective="learn")
    if not items:
        raise SystemExit(
            "no repair-eligible functions: every unsolved unsealed function either has no "
            "compiled attempt, no stored residual, or was filtered as a library TU")
    # Stratify by size so one stratum cannot dominate, then take round-robin. `candidates`
    # ranks by learnability, which is the right ordering WITHIN a stratum and the wrong one
    # across strata: it puts every 200-byte function ahead of every 4000-byte one.
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    sizes = {row[1]: row[2] for row in conn.execute("select addr, name, size from functions")}
    for item in items:
        item["size"] = sizes.get(item["name"], 0)
        item["tier"] = ("small" if item["size"] <= 256 else
                        "medium" if item["size"] <= 1024 else
                        "large" if item["size"] <= 4096 else "huge")
    buckets: dict[str, list[dict]] = {}
    for item in items:
        buckets.setdefault(item["tier"], []).append(item)
    order = ["small", "medium", "large", "huge"]
    taken: list[dict] = []
    while len(taken) < limit and any(buckets.get(t) for t in order):
        for tier in order:
            bucket = buckets.get(tier) or []
            if bucket and len(taken) < limit:
                taken.append(bucket.pop(0))
    return taken


def verify_lineage(db: Path) -> dict:
    """Check that every stored repair is linked to a parent its prompt actually quotes.

    This is the audit that would have caught the September 19 lineage defect on REAL data
    rather than in a unit test. For every attempt whose prompt is a repair prompt it asserts:

      - it has a parent receipt id, and that parent row exists;
      - the parent's exact source appears verbatim in the child's prompt;
      - the parent's own compiler outcome (its diff, or its stderr when it did not compile)
        appears in the prompt as the feedback;
      - an `attempt_edges` row exists for the pair.

    It deliberately does NOT require the child to score higher. An attempted repair that
    failed is still a real, correctly-linked attempt, and dropping it would hide the yield.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("""
        select c.id, c.parent_attempt_id, c.prompt_context, p.source_code,
               p.diff_summary, p.compiler_stderr, p.compiled
        from attempts c left join attempts p on p.id = c.parent_attempt_id
        where c.sampling like '%"role": "repair"%'""").fetchall()
    edges = {(row[0], row[1]) for row in conn.execute(
        "select parent_attempt_id, child_attempt_id from attempt_edges")}
    problems: list[dict] = []
    for cid, pid, prompt, psrc, pdiff, perr, pcomp in rows:
        if pid is None:
            problems.append({"child": cid, "problem": "repair prompt with no parent receipt"})
            continue
        if psrc is None:
            problems.append({"child": cid, "parent": pid, "problem": "parent row missing"})
            continue
        if not psrc or psrc not in (prompt or ""):
            problems.append({"child": cid, "parent": pid,
                             "problem": "parent source not verbatim in the repair prompt"})
        feedback = (pdiff or "") if pcomp else (perr or "")
        if feedback and feedback[:200] not in (prompt or ""):
            problems.append({"child": cid, "parent": pid,
                             "problem": "parent compiler feedback not in the prompt"})
        if (pid, cid) not in edges:
            problems.append({"child": cid, "parent": pid,
                             "problem": "no attempt_edges row for the pair"})
    return {"repair_attempts": len(rows), "edges": len(edges), "problems": problems,
            "ok": not problems}


def draw_seed(base: int | None, function: str, role: str, index: int) -> int | None:
    """A per-call seed derived from the function, the role and the draw index.

    THIS IS NOT BOOKKEEPING. `solver/llm.generate` documents that seeded generations may be
    cached and that "prompt-only caching would turn repeated stochastic draws into duplicate
    evidence and silently fabricate sample size". vLLM enforces the same discipline at the
    sampler: an explicit `seed` makes the request REPRODUCIBLE, so four draws that pass the
    same seed return the same text four times. The 2026-09-20 pilot did exactly that -- 68
    "independent" draws were 17 distinct generations repeated four times each, and all 12
    repair draws collapsed onto ONE output for each of three functions.

    Worse, the single output a fixed seed produced for a repair was the candidate itself,
    byte for byte: that seed is a fixed point of the repair prompt. So the batch reported a
    0/12 repair yield that was entirely an artifact of repeated seeds, and best-of-N looked
    like best-of-1.

    Deriving the seed per call keeps reproducibility where it is wanted (a seed still fixes
    the run) while making the draws actually independent. `None` stays `None`: an unseeded
    run is stochastic, which is also a valid configuration.
    """
    if base is None:
        return None
    material = f"{base}:{function}:{role}:{index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big") % (2 ** 31 - 1)


def run(args) -> dict:
    from eval import trajectory_factory as tf
    from solver import workspace as ws_mod

    spec = tf.GAMES["sbk1"]
    # Held out from TRAINING as well as from evaluation: an evaluation panel member that
    # appeared in the training data would make the final number meaningless.
    eval_panel = set()
    prereg = args.panel_preregistration
    if prereg and prereg.exists():
        manifest = json.loads(prereg.read_text(encoding="utf-8"))
        eval_panel = {row["function"] for row in manifest["panel"]}
    sealed = tf.sealed_functions() | eval_panel

    items = collection_pool(spec, sealed, spec.kb, limit=args.functions,
                            min_attempts=args.min_attempts,
                            exclude=set(args.exclude or []))
    if not items:
        raise SystemExit("no collection work items")

    args.out.mkdir(parents=True, exist_ok=True)
    state = args.out / "collected.state.jsonl"
    proposals = args.out / "collected.proposals.jsonl"
    done_functions = set()
    if state.exists():
        done_functions = {json.loads(line)["func"] for line in
                          state.read_text(encoding="utf-8").splitlines() if line.strip()}
    items = [item for item in items if item["name"] not in done_functions]
    print(json.dumps({"pool": len(items), "already_done": len(done_functions),
                      "tiers": {t: sum(1 for i in items if i["tier"] == t)
                                for t in ("small", "medium", "large", "huge")}}), flush=True)

    conn = scratch_db(args.scratch_db, spec.kb)
    # Register the function rows the receipts reference; the scratch DB starts empty.
    src = sqlite3.connect(f"file:{spec.kb}?mode=ro", uri=True)
    for addr, name, tu_id, size in src.execute(
            "select addr, name, tu_id, size from functions"):
        conn.execute("insert or ignore into functions (addr, name, tu_id, size)"
                     " values (?,?,?,?)", (addr, name, tu_id, size))
    for row in src.execute("select id, name from tus"):
        conn.execute("insert or ignore into tus (id, name) values (?,?)", row)
    conn.commit()
    src.close()

    generator = args.generator_factory(args)
    scorer = tf.WorkspaceScorer(spec.repo, args.scratch_db, strategy=args.strategy,
                                run_id=args.run_id)
    factory = tf.Factory(generator=generator, scorer=scorer,
                         context_for=tf.make_context(spec), rounds=args.rounds,
                         samples=args.samples, temperature=args.temperature,
                         game=spec.name, compiler=spec.compiler)

    def on_proposal(row: dict) -> None:
        with proposals.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def checkpoint(row: dict) -> None:
        with state.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)

    started = time.time()
    report = tf.run(factory, items, max_attempts=args.max_calls,
                    max_seconds=args.max_seconds, checkpoint=checkpoint,
                    on_proposal=on_proposal)
    report["yield"] = tf.yield_summary(report)
    report["wall_seconds"] = round(time.time() - started, 1)
    report["model"] = getattr(generator, "model", "")
    report["model_digest"] = (generator.model_digest()
                              if hasattr(generator, "model_digest") else "")
    report["scratch_db"] = str(args.scratch_db)
    report["panel_excluded"] = len(eval_panel)
    report["sealed_excluded"] = len(sealed)
    report["pool"] = [{"name": i["name"], "tier": i["tier"], "size": i["size"],
                       "best_score": i["best_score"]} for i in items]
    (args.out / "collection_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"yield": report["yield"], "stopped": report["stopped"],
                      "seconds": report["wall_seconds"]}, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scratch-db", type=Path, required=True)
    ap.add_argument("--panel-preregistration", type=Path, default=None,
                    help="exclude every function on the frozen evaluation panel")
    ap.add_argument("--functions", type=int, default=30)
    ap.add_argument("--min-attempts", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--samples", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--max-calls", type=int, default=500)
    ap.add_argument("--max-seconds", type=int, default=5400)
    ap.add_argument("--strategy", default="repair-collect")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--endpoint", default="http://127.0.0.1:8100")
    ap.add_argument("--model", default="/home/grant/decomp/models/qwen2.5-coder-7b")
    ap.add_argument("--adapter", default="")
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--verify", action="store_true",
                    help="audit the scratch DB's repair lineage and exit")
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args(argv)

    if args.verify:
        report = verify_lineage(args.scratch_db)
        print(json.dumps({k: v for k, v in report.items() if k != "problems"}, indent=2))
        for problem in report["problems"][:20]:
            print("  PROBLEM:", json.dumps(problem))
        return 0 if report["ok"] else 1

    if args.plan:
        from eval import trajectory_factory as tf
        spec = tf.GAMES["sbk1"]
        eval_panel = set()
        if args.panel_preregistration and args.panel_preregistration.exists():
            manifest = json.loads(args.panel_preregistration.read_text(encoding="utf-8"))
            eval_panel = {row["function"] for row in manifest["panel"]}
        items = collection_pool(spec, tf.sealed_functions() | eval_panel, spec.kb,
                                limit=args.functions, min_attempts=args.min_attempts,
                                exclude=set(args.exclude or []))
        print(json.dumps({"pool_size": len(items),
                          "tiers": {t: sum(1 for i in items if i["tier"] == t)
                                    for t in ("small", "medium", "large", "huge")},
                          "items": [{"name": i["name"], "tier": i["tier"],
                                     "size": i["size"], "best_score": i["best_score"],
                                     "owned_share": i["owned_share"],
                                     "learnability": i["learnability"]} for i in items]},
                         indent=2))
        return 0

    def generator_factory(a):
        from eval.inference_generator import ServeGenerator
        return ServeGenerator(endpoint=a.endpoint, model=a.model,
                              adapter=(a.adapter or None), max_tokens=a.max_tokens,
                              seed=a.seed)

    args.generator_factory = generator_factory
    report = run(args)
    return 0 if report.get("attempts") is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())

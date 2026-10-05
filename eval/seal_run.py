"""Run a generator against the sealed (or dev) near-miss split in one command.

    python3 -m eval.seal_run --manifest eval/sets/sbk1_v5_sealed_nearmiss.json --split dev \
        --treatment narrow_updates --budget 24 --out /home/grant/decomp/experiments/seal-run-X

``--split dev`` exercises the whole machinery without spending a look and is the default.
``--split sealed`` calls ``seal.look`` once, covering every sealed function: a function whose
arm raises is logged with its error and scored as baseline for BOTH arms, never dropped, so
a crash cannot flatter the treatment by shrinking the denominator.

Control and treatment are the same engine (``regalloc_search.search``) at the same budget from
the same pinned starting candidate; the only difference is the treatment keyword(s). Equal
compiler-call cost is checked from the arms' own counters, not asserted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import seal  # noqa: E402

MIN_DEV_DIVERGED = 5          # pre-registered: a treatment that fires on fewer dev functions is not lookable

LEDGER_PATHS = {"kb": "/home/grant/decomp/kb-sbk1.sqlite",
                "campaign": "/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"}


def pick_best(attempts: dict) -> str:
    """Best-scoring compiled attempt, exact first, earliest on ties. ``attempts``: source -> (id, score, exact).

    The registered metric is best score found, not the search's gradient-selected node.
    """
    return max(attempts, key=lambda c: (attempts[c][2], attempts[c][1], -attempts[c][0]))


def _err(exc: Exception) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()[:300]


def run_split(rows: list[dict], arm_fn, *, treatment: dict) -> tuple[dict, list[dict]]:
    """Run both arms for every row. ``arm_fn(row, kwargs) -> {ref, compiles, sources}``.

    Each arm has its own try block. A control crash ties both arms at the pinned start; a
    treatment-only crash keeps control's real result for both (a treatment failure, counted).
    ``sources`` (hashes of everything compiled) shows whether the treatment ever diverged from
    control: identical source lists mean the treatment did nothing, which is NOT "no effect".
    """
    outcomes, log = {}, []
    for row in rows:
        entry = {"function": row["function"], "tu": row["tu"]}
        control = treat = None
        try:
            control = arm_fn(row, {})
        except Exception as exc:                       # noqa: BLE001 - logged, never skipped
            entry["control_error"] = _err(exc)
        if control is not None:
            try:
                treat = arm_fn(row, treatment)
            except Exception as exc:                   # noqa: BLE001
                entry["treatment_error"] = _err(exc)
                treat = control                        # a tie, but flagged as a treatment failure
        else:
            control = treat = {"ref": row["start"], "compiles": 0, "sources": []}
        entry.update(control_compiles=control["compiles"], treatment_compiles=treat["compiles"],
                     diverged=("treatment_error" not in entry
                               and list(treat.get("sources", [])) != list(control.get("sources", []))),
                     tie=control["ref"] == treat["ref"])
        outcomes[row["function"]] = {"control": control["ref"], "treatment": treat["ref"]}
        log.append(entry)
    return outcomes, log


def summarize(log: list[dict]) -> dict:
    return {"functions": len(log), "diverged": sum(e["diverged"] for e in log),
            "ties": sum(e["tie"] for e in log),
            "control_errors": sum("control_error" in e for e in log),
            "treatment_errors": sum("treatment_error" in e for e in log)}


def spend_of(log: list[dict]) -> dict:
    return {e["function"]: {"control": e["control_compiles"], "treatment": e["treatment_compiles"]} for e in log}


def budget_of(log: list[dict], cap: int | None = None) -> dict:
    """Per-function cap each arm ran under; look() checks every function's actual spend against it."""
    if cap is not None:
        return {"control_compiles": cap + 1, "treatment_compiles": cap + 1}     # +1: the baseline compile
    return {"control_compiles": max((e["control_compiles"] for e in log), default=0),
            "treatment_compiles": max((e["treatment_compiles"] for e in log), default=0)}


def score_attempt_adapter(score_attempt):
    """site_edits.search wants score(code, label, parent_code) -> workspace.Attempt."""
    return lambda code, label, parent_code: score_attempt(code, label, parent_code)[0]


def native_arm_factory(out: Path, budget: int, ledgers: dict, trial: sqlite3.Connection):
    """IDO-backed arm: pinned start -> regalloc_search at ``budget`` -> best attempt in the trial DB."""
    import shutil
    from eval import campaign_workers
    from solver import regalloc_search as rs, workspace
    repo_root = Path("/home/grant/decomp/sbk1")

    def arm(row: dict, kwargs: dict) -> dict:
        name, label = row["function"], "treatment" if kwargs else "control"
        start = row["start"]
        source = ledgers[start["ledger"]].execute("SELECT source_code FROM attempts WHERE id=?",
                                                  (start["attempt_id"],)).fetchone()[0]
        if hashlib.sha256(source.encode()).hexdigest() != start["source_sha256"]:
            raise ValueError(f"{name}: pinned start source hash changed")
        folder = out / name / label
        folder.mkdir(parents=True, exist_ok=False)
        repo = campaign_workers.isolate(repo_root, folder / "repo", name)
        (repo / "tools").unlink()
        shutil.copytree(repo_root / "tools", repo / "tools", symlinks=True)
        ws = workspace.bootstrap(repo, name)
        target = (ws / "target_object_dump_normalized.s").read_text()
        ids: dict[str, tuple[int, float, bool]] = {}
        count = [0]

        def score_attempt(code, tag, parent_source=None):
            count[0] += 1
            attempt = workspace.score(ws, repo, f"seal_{count[0]:04d}", code, conn=trial, func=name,
                strategy=f"seal-run:{label}:{tag}"[:120], run_id="seal-run",
                parent_attempt_id=(ids.get(parent_source) or (None,))[0], relation="candidate-construction",
                extra={"training_eligible": False, "arm": label})
            trial.commit()
            exact = workspace.repair_complete(attempt)
            ids[code] = (attempt.receipt_id, float(attempt.score or 0.0), exact)
            return attempt, exact

        def evaluate(code, tag, parent_source=None):
            attempt, exact = score_attempt(code, tag, parent_source)
            normalized = ws / f"seal_{count[0]:04d}_object_dump_normalized.s"
            dump = normalized.read_text() if attempt.compiled and normalized.exists() else None
            return rs.Compiled(attempt.compiled, exact, dump, attempt.diff,
                evidence={"compiled": attempt.compiled, "frontend": attempt.frontend,
                          "source_attribution": attempt.source_attribution,
                          "compiler_recipe": attempt.compiler_recipe})

        kwargs = dict(kwargs)
        site_first = kwargs.pop("site_edits_first", False)
        search_budget, start_source = budget, source
        if site_first:
            from solver import site_edits
            half = budget // 2 - 1                      # baseline + edits + (rs baseline + edits) stays <= budget + 1
            found = site_edits.search(score_attempt_adapter(score_attempt), source, name, budget=half)
            start_source, search_budget = found["source"], budget - half - 1
        baseline = evaluate(start_source, "baseline") if start_source not in ids else evaluate(start_source, "rebaseline")
        outcome = rs.search(name, start_source, lambda c, l: evaluate(c, l), target, compile_with_parent=evaluate,
                            baseline=baseline, budget=search_budget, depth=4, beam=3, **kwargs)
        best = pick_best(ids)
        return {"ref": {"ledger": "trial", "attempt_id": ids[best][0]}, "compiles": count[0],
                "exact": ids[best][2], "score": ids[best][1],
                "sources": [hashlib.sha256(c.encode()).hexdigest()[:16] for c in ids]}
    return arm


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--split", choices=("dev", "sealed"), default="dev")
    ap.add_argument("--treatment", required=True, help="comma list of rs.search flags set True, e.g. narrow_updates")
    ap.add_argument("--budget", type=int, default=24)
    ap.add_argument("--limit", type=int, default=None, help="dev only: first N functions")
    ap.add_argument("--tool-file", action="append", default=[], help="files hashed into the look (default: solver/*.py touched)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", help="look ledger path (sealed only)")
    ap.add_argument("--dev-report", help="sealed only: report.json of a dev run of this same treatment; the treatment "
                    f"must have diverged from control on at least {MIN_DEV_DIVERGED} dev functions")
    args = ap.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if not seal.audit(manifest)["clean"]:
        raise SystemExit("manifest does not audit clean")
    if args.split == "sealed":
        if not args.ledger or not args.dev_report:
            raise SystemExit("--ledger and --dev-report are required for a sealed look")
        dev = json.loads(Path(args.dev_report).read_text(encoding="utf-8"))
        fired = dev.get("summary", {}).get("diverged", 0)
        if fired < MIN_DEV_DIVERGED:
            raise SystemExit(f"treatment diverged from control on {fired} dev functions (< {MIN_DEV_DIVERGED}); "
                             "it does not fire on near-misses, so a sealed look would measure a no-op")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    ledgers = {k: sqlite3.connect(f"file:{v}?mode=ro", uri=True) for k, v in LEDGER_PATHS.items()}
    trial = sqlite3.connect(out / "trial.sqlite", timeout=120)   # "database is locked" hit 4/95 dev functions
    trial.executescript((ROOT / "kb/schema.sql").read_text())
    trial.execute("ATTACH DATABASE ? AS origin", (f"file:{LEDGER_PATHS['kb']}?mode=ro",))
    for table in ("extraction", "tus", "functions"):
        trial.execute(f"INSERT INTO main.{table} SELECT * FROM origin.{table}")
    trial.commit()
    trial.execute("DETACH DATABASE origin")
    rows = manifest[args.split]
    rows = rows[:args.limit] if args.limit and args.split == "dev" else rows
    treatment = {flag: True for flag in args.treatment.split(",")}
    started = time.monotonic()
    outcomes, log = run_split(rows, native_arm_factory(out, args.budget, ledgers, trial), treatment=treatment)
    ledgers["trial"] = trial
    budget, spend, summary = budget_of(log, args.budget), spend_of(log), summarize(log)
    (out / "log.json").write_text(json.dumps(log, indent=2) + "\n")
    names = {r["function"] for r in rows}
    covariates, exclude = {}, set()
    addendum = ROOT / "eval/results/goal-20261002/addendum.json"
    if addendum.exists():
        facts = json.loads(addendum.read_text())
        cutoff, camp = facts["cutoff"]["campaign_max_attempt_id"], ledgers["campaign"]
        marks, params = ",".join("?" for _ in rows), [cutoff, *sorted(names)]
        post = dict(camp.execute(
            "SELECT f.name, COUNT(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr "
            f"WHERE a.id>? AND f.name IN ({marks}) GROUP BY f.name", params))
        exact_post = sorted(n for (n,) in camp.execute(
            "SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr "
            f"WHERE a.id>? AND a.exact=1 AND f.name IN ({marks})", params))
        ceiling = [n for n in facts["score_100_ceiling_rows"] if n in names]
        covariates = {"post_cutoff_campaign_attempts": sum(post.values()),
                      "functions_with_post_cutoff_attempts": len(post),
                      "campaign_exact_after_cutoff": exact_post, "score_100_ceiling_rows": ceiling}
        exclude = set(exact_post) | set(ceiling)
    extra = {"summary": summary, "covariates": covariates,
             "errors": {e["function"]: e.get("control_error") or e.get("treatment_error")
                        for e in log if "control_error" in e or "treatment_error" in e}}
    spec = {"treatment": treatment, "budget": args.budget, "depth": 4, "beam": 3, "engine": "regalloc_search.search"}
    if args.split == "sealed":
        files = [Path(p) for p in args.tool_file] or sorted((ROOT / "solver").glob("*.py"))
        entry = seal.look(manifest, Path(args.ledger), ledgers, tool=args.treatment, tool_files=files,
                          outcomes=outcomes, budget=budget, spec=spec, spend=spend, extra=extra,
                          exclude=exclude, note=f"seal_run budget={args.budget}")
        report = entry["report"]
        report["report_excluding"] = entry.get("report_excluding")
    else:
        # dev: same checked arithmetic, no ledger entry, no look spent
        by = {r["function"]: r for r in rows}
        rec = [{"function": n, "tu": by[n]["tu"], "tier": by[n]["tier"],
                "control": seal._arm(ledgers, o["control"], n), "treatment": seal._arm(ledgers, o["treatment"], n)}
               for n, o in outcomes.items()]
        report = seal.paired_report(rec)
    report.update(extra)
    report["budget"], report["spend_max"] = budget, max((max(v.values()) for v in spend.values()), default=0)
    report["seconds"] = time.monotonic() - started
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

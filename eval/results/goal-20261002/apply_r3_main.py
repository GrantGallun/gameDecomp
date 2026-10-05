"""Rewrite the tail of eval/seal_run.main: divergence gate, spec hash, extras inside the ledger entry."""
from pathlib import Path

p = Path(__file__).resolve().parents[3] / 'eval/seal_run.py'
s = p.read_text(encoding='utf-8')

s = s.replace('''    ap.add_argument("--ledger", help="look ledger path (sealed only)")
''', '''    ap.add_argument("--ledger", help="look ledger path (sealed only)")
    ap.add_argument("--dev-report", help="sealed only: report.json of a dev run of this same treatment; the treatment "
                    f"must have diverged from control on at least {MIN_DEV_DIVERGED} dev functions")
''', 1)
s = s.replace('''LEDGER_PATHS = {''', '''MIN_DEV_DIVERGED = 5          # pre-registered: a treatment that fires on fewer dev functions is not lookable

LEDGER_PATHS = {''', 1)
s = s.replace('''    if args.split == "sealed" and not args.ledger:
        raise SystemExit("--ledger is required for a sealed look")
''', '''    if args.split == "sealed":
        if not args.ledger or not args.dev_report:
            raise SystemExit("--ledger and --dev-report are required for a sealed look")
        dev = json.loads(Path(args.dev_report).read_text(encoding="utf-8"))
        fired = dev.get("summary", {}).get("diverged", 0)
        if fired < MIN_DEV_DIVERGED:
            raise SystemExit(f"treatment diverged from control on {fired} dev functions (< {MIN_DEV_DIVERGED}); "
                             "it does not fire on near-misses, so a sealed look would measure a no-op")
''', 1)

a = s.index('    treatment = {flag: True')
tail = '''    treatment = {flag: True for flag in args.treatment.split(",")}
    started = time.monotonic()
    outcomes, log = run_split(rows, native_arm_factory(out, args.budget, ledgers, trial), treatment=treatment)
    ledgers["trial"] = trial
    budget, spend, summary = budget_of(log, args.budget), spend_of(log), summarize(log)
    (out / "log.json").write_text(json.dumps(log, indent=2) + "\\n")
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
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
p.write_text(s[:a] + tail, encoding='utf-8')

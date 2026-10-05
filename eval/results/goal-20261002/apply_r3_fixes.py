"""One-off patch applying the round-3 audit fixes to eval/seal.py and eval/seal_run.py."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SEAL_LOOK = '''def look(manifest: dict, ledger: Path, ledgers: dict[str, sqlite3.Connection], *, tool: str,
         tool_files: list[Path], outcomes: dict[str, dict], budget: dict, spec: dict | None = None,
         spend: dict | None = None, extra: dict | None = None, exclude: set[str] | None = None,
         note: str = "") -> dict:
    """One evaluation of a tool on the sealed side.

    ``outcomes[name] = {"control": {ledger, attempt_id}, "treatment": {ledger, attempt_id}}``
    and ``budget = {"control_compiles": n, "treatment_compiles": m}`` (must be equal: the claim
    is "same cap per function, per arm; actual spend reported"). ``spend[name] = {"control": c,
    "treatment": t}`` is checked against the cap per function and stored. ``spec`` (treatment
    kwargs, budget, depth, beam...) is hashed with the tool files so two treatments on one tree
    are different tools. ``extra`` (errors, ties, divergence) is written INSIDE the entry before
    it is hashed. ``exclude`` names are dropped for a second, pre-declared sign test.
    """
    names = sealed_names(manifest)
    if set(outcomes) != names:
        raise ValueError(f"look must cover exactly the sealed set: missing {len(names - set(outcomes))}, "
                         f"extra {len(set(outcomes) - names)}")
    if budget.get("control_compiles") != budget.get("treatment_compiles"):
        raise ValueError("arms must have equal compiler-call budgets")
    cap = budget["control_compiles"]
    over = sorted(n for n, sp in (spend or {}).items() if max(sp.get("control", 0), sp.get("treatment", 0)) > cap)
    if over:
        raise ValueError(f"per-function spend exceeds the cap {cap}: {over[:5]}")
    check = verify_ledger(ledger, manifest)
    if not check["valid"]:
        raise RuntimeError(f"ledger invalid at {check['bad_entries']}")
    entries = _read(ledger)
    if len(entries) >= manifest["max_looks"]:
        raise RuntimeError(f"seal spent: {len(entries)} of {manifest['max_looks']} looks used")
    rows, by_name = [], {r["function"]: r for r in manifest["sealed"]}
    for name in sorted(names):
        control = _arm(ledgers, outcomes[name]["control"], name)
        treat = _arm(ledgers, outcomes[name]["treatment"], name)
        for label, arm in (("control", control), ("treatment", treat)):
            if arm["exact"] and arm["score"] != 100.0:
                raise ValueError(f"{name}: {label} exact attempt with score {arm['score']}")
        rows.append({"function": name, "tu": by_name[name]["tu"], "tier": by_name[name]["tier"],
                     "start": by_name[name]["start"]["source_sha256"], "control": control, "treatment": treat,
                     "refs": outcomes[name]})
    tool_hash = hashlib.sha256(("".join(file_sha256(p) for p in sorted(tool_files))
                                + json.dumps(spec or {}, sort_keys=True)).encode()).hexdigest()
    entry = {"manifest": manifest["manifest_digest"], "tool": tool, "tool_sha256": tool_hash, "spec": spec or {},
             "at": int(time.time()), "look_number": len(entries) + 1,
             "repeat_of_same_tool": any(e["tool_sha256"] == tool_hash for e in entries),
             "budget": budget, "spend": spend or {}, "extra": extra or {}, "rows": rows,
             "report": paired_report(rows), "note": note,
             "prev": entries[-1]["entry"] if entries else manifest["manifest_digest"]}
    if exclude:
        entry["report_excluding"] = {"excluded": sorted(exclude & names),
                                     **paired_report([r for r in rows if r["function"] not in exclude])}
    entry["entry"] = _digest(entry, "entry")
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\\n")
    return entry


'''

RUN_SPLIT = '''def pick_best(attempts: dict) -> str:
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


'''


def main():
    p = ROOT / 'eval/seal.py'
    s = p.read_text(encoding='utf-8')
    a, b = s.index('def look('), s.index('def paired_report')
    p.write_text(s[:a] + SEAL_LOOK + s[b:], encoding='utf-8')

    p = ROOT / 'eval/seal_run.py'
    s = p.read_text(encoding='utf-8')
    a, b = s.index('def run_split'), s.index('def native_arm_factory')
    s = s[:a] + RUN_SPLIT + s[b:]
    old = '''        best = outcome.best_source if outcome.best_source in ids else source
        return {"ref": {"ledger": "trial", "attempt_id": ids[best][0]}, "compiles": count[0],
                "exact": ids[best][2], "score": ids[best][1]}'''
    new = '''        best = pick_best(ids)
        return {"ref": {"ledger": "trial", "attempt_id": ids[best][0]}, "compiles": count[0],
                "exact": ids[best][2], "score": ids[best][1],
                "sources": [hashlib.sha256(c.encode()).hexdigest()[:16] for c in ids]}'''
    assert old in s
    p.write_text(s.replace(old, new), encoding='utf-8')


main()

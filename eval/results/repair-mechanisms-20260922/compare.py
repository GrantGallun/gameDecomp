"""Paired compiler-bounded search with exact parent IDs captured at generation."""
import hashlib
import json
import sqlite3
from probe import OUT, NATIVE, REPO, ROOT, isolate, score_logged, _attempt_to_verdict
from solver import regalloc_mutations, regalloc_search

NEW_FAMILIES = {"unsigned_float", "cursor_rebase", "residual_evidence"}
NAMES = ["Fvibup", "Fvibdown", "releaseMenuAssetHandles", "loadMusicSequenceBank",
         "FrandPan", "allocTranslationOnlyFixedMatrix", "Fdistort", "__MusIntProcessWobble"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def main():
    target_out = OUT / "comparison"
    target_out.mkdir(exist_ok=True)
    if (target_out / "report.json").exists():
        raise SystemExit("preserve prior comparison")
    frozen = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in [
        "solver/representation_repairs.py", "solver/regalloc_mutations.py", "solver/regalloc_search.py"]}
    report = {"training_eligible": False, "budget_per_arm_including_baseline": 60,
              "implementation_sha256": frozen, "arms": []}
    (target_out / "preregistration.json").write_text(json.dumps(report, indent=2))
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    original = regalloc_mutations.variants
    try:
        for name in NAMES:
            source_path = OUT / "inputs" / name / "initial.c"
            if not source_path.exists():
                source_path = ROOT / "eval/results/dev-set-20260921/sources" / f"{name}.c"
            source = source_path.read_text()
            for arm in ("control", "expanded"):
                repo = isolate(REPO, NATIVE / "comparison" / name / arm, name)
                ws = repo / "nonmatchings" / name
                rows, receipts, parents = [], {}, {}
                def proposals(code, function, diff="", prefer=()):
                    for label, kind, child in original(code, function, diff, prefer):
                        if arm == "control" and kind in NEW_FAMILIES:
                            continue
                        parents[sha(child)] = receipts[sha(code)]
                        yield label, kind, child
                regalloc_mutations.variants = proposals
                def compile_one(code, label):
                    if len(rows) >= 60:
                        raise RuntimeError("compile allowance exhausted")
                    parent = parents.get(sha(code))
                    if label != "baseline":
                        assert parent is not None
                    attempt = score_logged(ws, repo, name, code, conn=conn,
                        strategy=f"repair-mechanisms:{arm}:{label}", run_id=f"repair-mechanisms:{name}:{arm}",
                        parent_attempt_id=parent, action=label, model="deterministic-search",
                        prompt="Frozen generated development source, target assembly and parent compiler diff.",
                        extra={"training_eligible": False, "assistance_tier": "project-header-assisted"})
                    verdict = _attempt_to_verdict(attempt)
                    exact = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
                    dump = ws / f"{name}_object_dump_normalized.s"
                    receipts[sha(code)] = attempt.receipt_id
                    rows.append({"receipt_id": attempt.receipt_id, "parent_receipt_id": parent,
                                 "source_sha256": sha(code), "exact": exact, "score": attempt.score,
                                 "compiled": attempt.compiled, "label": label})
                    (target_out / f"{name}--{attempt.receipt_id}.json").write_text(json.dumps(verdict, indent=2))
                    return regalloc_search.Compiled(attempt.compiled, exact,
                        dump.read_text() if attempt.compiled and dump.exists() else None, attempt.diff)
                base = compile_one(source, "baseline")
                result = regalloc_search.search(name, source, compile_one,
                    (ws / "target_object_dump_normalized.s").read_text(), baseline=base, budget=59, depth=4, beam=3)
                (target_out / f"{name}--{arm}.c").write_text(result.best_source)
                row = {"function": name, "arm": arm, "compiles": len(rows), "exact": result.exact,
                       "best_score_observed": max(r["score"] for r in rows),
                       "source_sha256": sha(result.best_source), "final_receipt_id": receipts[sha(result.best_source)],
                       "attempts": rows, "search": result.summary()}
                report["arms"].append(row)
                (target_out / "report.json").write_text(json.dumps(report, indent=2))
                print(json.dumps({k: v for k, v in row.items() if k not in {"attempts", "search"}}), flush=True)
        assert frozen == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in frozen}
        report["complete"] = True
        (target_out / "report.json").write_text(json.dumps(report, indent=2))
    finally:
        regalloc_mutations.variants = original
        conn.close()


if __name__ == "__main__":
    main()

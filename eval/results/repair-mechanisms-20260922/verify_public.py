"""Exercise the public tool action and freshly confirm source-bound successes."""
import hashlib
import json
import sqlite3
from probe import OUT, NATIVE, REPO, ROOT, isolate, score_logged, _attempt_to_verdict
from solver import regalloc_mutations
from eval.tool_agent import Context, ScriptedPolicy, run_episode


def sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def main():
    if (OUT / "public-verification.json").exists():
        raise SystemExit("preserve existing public verification")
    comparison = json.loads((OUT / "comparison/report.json").read_text())
    assert comparison["complete"]
    for path, digest in comparison["implementation_sha256"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    original = regalloc_mutations.variants
    results = {"public_actions": [], "confirmations": [], "training_eligible": False}
    try:
        for name in ("Fvibup", "Fvibdown"):
            source = (OUT / "inputs" / name / "initial.c").read_text()
            repo = isolate(REPO, NATIVE / "public-action" / name, name)
            ws = repo / "nonmatchings" / name
            rows, receipts, parents = [], {}, {}
            def variants(code, function, diff="", prefer=()):
                for label, kind, child in original(code, function, diff, prefer):
                    parents[sha(child)] = receipts[sha(code)]
                    yield label, kind, child
            regalloc_mutations.variants = variants
            def compile_one(code):
                assert len(rows) < 63
                parent = parents.get(sha(code), receipts.get(sha(code)))
                attempt = score_logged(ws, repo, name, code, conn=conn,
                    strategy="repair-mechanisms:public-regalloc", run_id="repair-mechanisms-public:" + name,
                    parent_attempt_id=parent, action="regalloc-search", model="deterministic-tool-controller",
                    prompt="Public action on the frozen development draft; source-bound compiler feedback.",
                    extra={"training_eligible": False, "assistance_tier": "source-independent"})
                verdict = _attempt_to_verdict(attempt)
                verdict["exact"] = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
                dump = ws / f"{name}_object_dump_normalized.s"
                verdict["dump"] = dump.read_text() if attempt.compiled and dump.exists() else ""
                receipts[sha(code)] = attempt.receipt_id
                rows.append({"receipt_id": attempt.receipt_id, "parent_receipt_id": parent,
                             "source_sha256": sha(code), "exact": verdict["exact"]})
                return verdict
            base = compile_one(source)
            context = Context(function=name, candidate=source, compile_fn=compile_one,
                target_dump=(ws / "target_object_dump_normalized.s").read_text(), diff=base["diff"],
                initial_verdict=base)
            emitted = []
            trace = run_episode(context, ScriptedPolicy(order=[("regalloc-search", {"budget": 60, "beam": 3})]),
                                budget=1, on_candidate=emitted.append)
            assert trace.exact and emitted[-1] == context.candidate
            assert context.initial_verdict["source_sha256"] == sha(emitted[-1])
            assert trace.steps[-1].candidate_sha256 == sha(emitted[-1])
            (OUT / f"{name}--public.c").write_text(emitted[-1])
            results["public_actions"].append({"function": name, "exact": trace.exact, "compiles": len(rows),
                "source_sha256": sha(emitted[-1]), "receipt_id": receipts[sha(emitted[-1])],
                "attempts": rows, "transcript": trace.as_dict()})
            (OUT / "public-verification.json").write_text(json.dumps(results, indent=2))
            print(json.dumps({"function": name, "public_exact": trace.exact, "compiles": len(rows)}), flush=True)
    finally:
        regalloc_mutations.variants = original
    successes = {r["function"]: r for r in comparison["arms"] if r["exact"]}
    for name, row in successes.items():
        source = (OUT / "comparison" / f"{name}--{row['arm']}.c").read_text()
        assert sha(source) == row["source_sha256"]
        repo = isolate(REPO, NATIVE / "confirmation" / name, name)
        attempt = score_logged(repo / "nonmatchings" / name, repo, name, source, conn=conn,
            strategy="repair-mechanisms:independent-confirmation", run_id="repair-mechanisms-confirm:" + name,
            parent_attempt_id=row["final_receipt_id"], action="independent-confirmation",
            model="independent-compiler", prompt="Fresh compilation of frozen source, no reference body.",
            extra={"training_eligible": False,
                   "assistance_tier": "project-header-assisted" if '#include "game/' in source else "source-independent"})
        verdict = _attempt_to_verdict(attempt)
        (OUT / f"{name}--confirmed.json").write_text(json.dumps(verdict, indent=2))
        assert attempt.exact and attempt.frontend["passed"]
        results["confirmations"].append({"function": name, "source_sha256": sha(source),
            "source_path": f"comparison/{name}--{row['arm']}.c", "receipt_id": attempt.receipt_id,
            "parent_receipt_id": row["final_receipt_id"], "exact": True})
        (OUT / "public-verification.json").write_text(json.dumps(results, indent=2))
    results["complete"] = True
    results["private_compiles"] = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    (OUT / "public-verification.json").write_text(json.dumps(results, indent=2))
    conn.close()


if __name__ == "__main__":
    main()

"""Paired old narrow lane versus the wired shape-repair action; fresh certificates."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent import Context, ScriptedPolicy, run_episode
from eval.tool_agent_run import _attempt_to_verdict
from solver import regalloc_search, workspace
from logged_compile import score_logged

EXPERIMENT = Path(__file__).resolve().parent
OUT = EXPERIMENT / "verified"
NATIVE = Path.home() / "decomp/experiments/repair-coverage-20260922"
REPO = Path.home() / "decomp/sbk1"
NAMES = ["__ll_rem", "__ull_divremi", "__ll_mod", "__ull_rem", "__ull_div", "__ll_mul", "__ull_rshift"]
FRESH = ["__ll_mul", "__ull_rshift"]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2))


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT / "verification.json").exists():
        raise SystemExit("verification already recorded; do not overwrite")
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
        ROOT / "solver/wide_runtime_interfaces.py", ROOT / "eval/tool_runners.py",
        ROOT / "eval/tool_registry.py", ROOT / "eval/tool_agent.py", Path(__file__)]}
    report = {"training_eligible": False, "budget_per_arm": 40, "fresh_development_functions": FRESH,
              "implementation_sha256": frozen, "arms": [], "confirmations": []}
    write("preregistration.json", report)
    successful = {}
    for name in NAMES:
        initial = EXPERIMENT / f"{name}--initial.c"
        if not initial.exists():
            initial.write_text((REPO / "nonmatchings" / name / "base.c").read_text())
        source = initial.read_text()
        for arm in ("regalloc-only", "wired-reconstruction"):
            repo = isolate(REPO, NATIVE / "verified" / "paired" / name / arm, name)
            ws = repo / "nonmatchings" / name
            rows = []
            parent = None
            def compile_candidate(code, label="reconstruct-wide"):
                nonlocal parent
                if len(rows) >= 40:
                    raise RuntimeError("compile ceiling reached")
                kw = dict(strategy=f"repair-coverage:{arm}:{label}",
                          run_id=f"repair-coverage-verified:{name}:{arm}", action=label,
                          parent_attempt_id=parent if arm == "wired-reconstruction" else None,
                          model="deterministic-tools", prompt="Frozen development state plus target assembly; no reference body.",
                          extra={"training_eligible": False, "assistance_tier": "source-independent",
                                 "lineage_status": "explicit" if arm == "wired-reconstruction" else "legacy-callback-unknown"})
                attempt = score_logged(ws, repo, name, code, conn=conn, **kw)
                assert attempt.receipt_id is not None
                if parent is None:
                    parent = attempt.receipt_id
                verdict = _attempt_to_verdict(attempt)
                verdict["exact"] = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
                path = ws / f"{name}_object_dump_normalized.s"
                verdict["dump"] = path.read_text() if attempt.compiled and path.exists() else None
                rows.append({"source_sha256": hashlib.sha256(code.encode()).hexdigest(),
                             "receipt_id": attempt.receipt_id, "label": label, "exact": verdict["exact"], "score": attempt.score})
                write(f"{name}--{arm}--{attempt.receipt_id}.json", verdict)
                return verdict
            base = compile_candidate(source, "baseline")
            if arm == "regalloc-only":
                def build(code, label):
                    v = compile_candidate(code, label)
                    return regalloc_search.Compiled(v["compiled"], v["exact"], v["dump"], v["diff"])
                result = regalloc_search.search(name, source, build,
                    (ws / "target_object_dump_normalized.s").read_text(),
                    baseline=regalloc_search.Compiled(base["compiled"], base["exact"], base["dump"], base["diff"]),
                    budget=39, beam=3, depth=4)
                exact, best_source, trace = result.exact, result.best_source, result.summary()
            else:
                context = Context(function=name, candidate=source, workspace=str(ws), repo=str(repo),
                                  target_asm_path=str(ws / "target.s"), initial_verdict=base,
                                  compile_fn=compile_candidate, diff=base["diff"],
                                  target_dump=(ws / "target_object_dump_normalized.s").read_text())
                exported = []
                result = run_episode(context, ScriptedPolicy(), budget=1, on_candidate=exported.append)
                best_source = exported[-1] if exported else source
                exact, trace = result.exact, result.as_dict()
                assert result.steps[-1].action == "reconstruct-wide"
                digest = hashlib.sha256(best_source.encode()).hexdigest()
                assert context.candidate == best_source
                assert result.steps[-1].candidate_sha256 == digest
                assert context.initial_verdict["source_sha256"] == digest
                assert any(r["exact"] and r["source_sha256"] == digest for r in rows) == exact
            (OUT / f"{name}--{arm}.c").write_text(best_source)
            row = {"function": name, "arm": arm, "compiles": len(rows), "exact": exact,
                   "best_score_observed": max(r["score"] for r in rows), "attempts": rows, "trace": trace,
                   "source_sha256": hashlib.sha256(best_source.encode()).hexdigest()}
            report["arms"].append(row)
            write("verification.json", report)
            print(json.dumps({k: v for k, v in row.items() if k not in {"attempts", "trace"}}), flush=True)
            if exact:
                receipt = next(r["receipt_id"] for r in rows if r["exact"] and r["source_sha256"] == row["source_sha256"])
                successful[name] = (best_source, receipt)
    for name, (source, parent_receipt) in successful.items():
        repo = isolate(REPO, NATIVE / "verified" / "confirm" / name, name)
        ws = repo / "nonmatchings" / name
        attempt = score_logged(ws, repo, name, source, conn=conn,
            strategy="repair-coverage:independent-confirmation", run_id=f"repair-coverage-confirm:{name}",
            action="independent-confirmation", parent_attempt_id=parent_receipt,
            model="independent-compiler", prompt="Recompile frozen candidate in a fresh workspace.",
            extra={"training_eligible": False, "assistance_tier": "source-independent"})
        verdict = _attempt_to_verdict(attempt)
        write(f"{name}--confirmed.json", verdict)
        assert attempt.exact and attempt.frontend["passed"]
        report["confirmations"].append({"function": name, "receipt_id": attempt.receipt_id,
            "parent_receipt_id": parent_receipt,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "exact": True})
        write("verification.json", report)
    assert len(successful) == len(NAMES)
    assert frozen == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in frozen}
    report["all_confirmed"] = True
    report["total_private_compiles_including_probe"] = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    write("verification.json", report)
    print(json.dumps({"all_confirmed": True, "functions": len(successful),
                      "total_private_compiles": report["total_private_compiles_including_probe"]}), flush=True)
    conn.close()


if __name__ == "__main__":
    main()

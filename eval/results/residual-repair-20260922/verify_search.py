"""Paired fixed-budget search and fresh independent-workspace certificates.

Uses only frozen development candidates. No training, reference bodies, or
production writes. Logs baseline, failed, and successful compiles privately.
"""
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import regalloc_mutations, regalloc_search, workspace

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/residual-repair-20260922"
REPO = Path.home() / "decomp/sbk1"
DEV = ROOT / "eval/results/dev-set-20260921"


def main():
    conn = sqlite3.connect(NATIVE / "attempts.sqlite")
    previous = OUT / "paired-search.json"
    archived = OUT / "paired-search-before-review.json"
    if previous.exists() and not archived.exists():
        old = json.loads(previous.read_text())
        archived.write_text(json.dumps(old, indent=2))
        corrections = []
        for arm in old["arms"]:
            rows = arm["attempts"]
            for row in rows:
                run = conn.execute("SELECT run_id FROM attempts WHERE id=?", (row["receipt_id"],)).fetchone()
                assert run == (f"cursor-paired-20260922:{arm['arm']}",)
                conn.execute("DELETE FROM attempt_edges WHERE child_attempt_id=?", (row["receipt_id"],))
            for index, node in enumerate(arm["search_log"], 1):
                parents = [r for r in rows[:index] if r["label"] == node.get("parent")]
                if len(parents) == 1:
                    conn.execute("INSERT INTO attempt_edges (parent_attempt_id,child_attempt_id,relation,action,feedback,created_at) VALUES (?,?,?,?,?,?)",
                        (parents[0]["receipt_id"], rows[index]["receipt_id"], "derive", node["label"],
                         json.dumps(node), int(time.time())))
                corrections.append({"child_receipt_id": rows[index]["receipt_id"],
                                    "parent_receipt_id": parents[0]["receipt_id"] if len(parents) == 1 else None})
        conn.commit()
        (OUT / "lineage-correction.json").write_text(json.dumps({
            "reason": "Repeated action labels were not unique node identities; reconstructed only unambiguous edges. All attempt rows and compiler verdicts retained.",
            "edges": corrections}, indent=2))
    original = regalloc_mutations.variants
    report = {"kind": "paired-development-search", "training_eligible": False,
              "budget_per_arm": 40, "beam": 3, "depth": 4, "arms": [], "verification": []}
    name = "Fdistort"
    source = (DEV / "sources" / f"{name}.c").read_text()
    for enabled in (False, True):
        arm = "with-cursor" if enabled else "without-cursor"
        repo = isolate(REPO, NATIVE / "paired" / arm, name)
        ws = repo / "nonmatchings" / name
        rows = []

        def compile_candidate(code, label):
            attempt = workspace.score(ws, repo, name, code, conn=conn, func=name,
                strategy=f"cursor-paired:{arm}:{label}", run_id=f"cursor-paired-20260922:{arm}",
                model="deterministic-search", prompt="Frozen Fdistort development state; paired 40-compile regalloc search.",
                extra={"training_eligible": False, "arm": arm, "assistance": "header-assisted"})
            digest = hashlib.sha256(code.encode()).hexdigest()
            rows.append({"label": label, "receipt_id": attempt.receipt_id, "sha256": digest,
                         "compiled": attempt.compiled, "exact": attempt.exact, "score": attempt.score})
            dump = ws / f"{name}_object_dump_normalized.s"
            return regalloc_search.Compiled(attempt.compiled, attempt.exact,
                dump.read_text() if attempt.compiled and dump.exists() else None, attempt.diff)

        def filtered(*args, **kwargs):
            for row in original(*args, **kwargs):
                if enabled or row[1] != "cursor_advance":
                    yield row

        started = time.monotonic()
        regalloc_mutations.variants = filtered
        try:
            baseline = compile_candidate(source, "baseline")
            target = (ws / "target_object_dump_normalized.s").read_text()
            result = regalloc_search.search(name, source, compile_candidate, target,
                budget=39, beam=3, depth=4, baseline=baseline)
        finally:
            regalloc_mutations.variants = original
        # Labels can repeat across branches. Pair children by compile order;
        # record a parent only when its prior label resolves unambiguously.
        for index, row in enumerate(result.log, 1):
            parents = [r for r in rows[:index] if r["label"] == row.get("parent")]
            assert rows[index]["label"] == row["label"]
            if len(parents) == 1:
                conn.execute("INSERT OR IGNORE INTO attempt_edges (parent_attempt_id,child_attempt_id,relation,action,feedback,created_at) VALUES (?,?,?,?,?,?)",
                    (parents[0]["receipt_id"], rows[index]["receipt_id"], "derive", row["label"], json.dumps(row), int(time.time())))
            else:
                row["lineage_status"] = "ambiguous-parent-label; no edge inferred"
        conn.commit()
        (OUT / f"Fdistort--search-{arm}.c").write_text(result.best_source)
        row = {"arm": arm, **result.summary(), "total_compiles_including_baseline": len(rows),
               "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
               "best_sha256": hashlib.sha256(result.best_source.encode()).hexdigest(),
               "seconds": round(time.monotonic() - started, 3), "attempts": rows, "search_log": result.log}
        report["arms"].append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("attempts", "search_log")}), flush=True)
        (OUT / "paired-search.json").write_text(json.dumps(report, indent=2))

    cases = [("Fdistort", "search-with-cursor", "header-assisted"),
             ("osEPiRawWriteIo", "literal-status-address", "binary-only"),
             ("osEPiRawReadIo", "literal-status-address", "binary-only")]
    for name, label, assistance in cases:
        repo = isolate(REPO, NATIVE / "independent-verify" / name, name)
        ws = repo / "nonmatchings" / name
        code = (OUT / f"{name}--{label}.c").read_text()
        attempt = workspace.score(ws, repo, name, code, conn=conn, func=name,
            strategy="independent-reverification", run_id="residual-reverify-20260922",
            model="zero-model", prompt="Fresh workspace recompile of the frozen successful candidate.",
            extra={"training_eligible": False, "assistance": assistance})
        verdict = _attempt_to_verdict(attempt)
        (OUT / f"{name}--independent-verification.json").write_text(json.dumps(verdict, indent=2))
        verification = attempt.verification or {}
        boundary = verification.get("function_boundary") or {}
        row = {"function": name, "source_sha256": hashlib.sha256(code.encode()).hexdigest(),
               "object_exact": attempt.exact, "function_exact": boundary.get("function_exact", False),
               "boundary_schema": boundary.get("schema_version"), "frontend_passed": (attempt.frontend or {}).get("passed"),
               "assistance": assistance, "receipt_id": attempt.receipt_id}
        report["verification"].append(row)
        print(json.dumps(row), flush=True)
    report["all_verified"] = all(r["frontend_passed"] and (r["object_exact"] or (r["function_exact"] and r["boundary_schema"] == 3))
                                  for r in report["verification"])
    (OUT / "paired-search.json").write_text(json.dumps(report, indent=2))
    conn.close()
    assert report["all_verified"], "fresh certificate failed"
    assert not report["arms"][0]["exact"] and report["arms"][1]["exact"], "paired search did not demonstrate gain"


if __name__ == "__main__":
    main()

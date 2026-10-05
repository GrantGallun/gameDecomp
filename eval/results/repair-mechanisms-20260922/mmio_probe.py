"""Public MMIO action, motivating cases, sibling declines, and fresh certificates."""
import hashlib
import json
import sqlite3
import sys
from probe import OUT, NATIVE, ROOT, REPO, isolate, score_logged, _attempt_to_verdict
from solver import workspace
from eval.tool_agent import Context, ScriptedPolicy, run_episode


def sha(source):
    return hashlib.sha256(source.encode()).hexdigest()


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "mmio"
    assert phase in {"mmio", "mmio-reviewed"}
    out = OUT / phase
    out.mkdir(exist_ok=True)
    native = NATIVE / phase
    native.mkdir(exist_ok=True)
    db = native / "attempts.sqlite"
    if db.exists():
        raise SystemExit("preserve prior MMIO receipts")
    conn = sqlite3.connect(db)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{NATIVE / 'attempts.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        conn.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    conn.commit()
    report = {"training_eligible": False, "rows": [], "confirmations": [],
              "implementation_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in
                  ["solver/mmio_repair.py", "eval/tool_agent.py", "eval/tool_runners.py", "eval/tool_registry.py"]}}
    def save():
        (out / "report.json").write_text(json.dumps(report, indent=2))
    for name in ("osEPiRawReadIo", "osEPiRawWriteIo", "osPiRawReadIo", "__osSiRawReadIo", "__osSiRawWriteIo"):
        repo = isolate(REPO, native / "public" / name, name)
        ws = repo / "nonmatchings" / name
        path = OUT / "inputs" / name / "initial.c"
        source = path.read_text() if path.exists() else workspace.m2c_draft(ws)
        assert source
        (out / f"{name}--initial.c").write_text(source)
        parent, rows = None, []
        def compile_one(code):
            nonlocal parent
            attempt = score_logged(ws, repo, name, code, conn=conn,
                strategy="repair-mechanisms:mmio-public", run_id="repair-mechanisms-mmio:" + name,
                parent_attempt_id=parent, action="baseline" if parent is None else "repair-mmio",
                model="deterministic-tool", prompt="Generated draft and encoded target assembly; no reference body.",
                extra={"training_eligible": False, "assistance_tier": "source-independent"})
            if parent is None:
                parent = attempt.receipt_id
            verdict = _attempt_to_verdict(attempt)
            verdict["exact"] = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
            (out / f"{name}--{attempt.receipt_id}.json").write_text(json.dumps(verdict, indent=2))
            rows.append({"receipt_id": attempt.receipt_id, "source_sha256": sha(code),
                         "compiled": attempt.compiled, "score": attempt.score, "object_exact": verdict["exact"]})
            return verdict
        base = compile_one(source)
        context = Context(function=name, candidate=source, initial_verdict=base,
                          compile_fn=compile_one, workspace=str(ws), target_asm_path=str(ws / "target.s"))
        transcript = run_episode(context, ScriptedPolicy(order=[("repair-mmio", {})]), budget=1)
        final = context.candidate
        (out / f"{name}--final.c").write_text(final)
        row = {"function": name, "changed": final != source, "calls": len(rows), "attempts": rows,
               "source_sha256": sha(final), "transcript": transcript.as_dict()}
        report["rows"].append(row)
        save()
        print(json.dumps({k: v for k, v in row.items() if k not in {"transcript", "attempts"}}), flush=True)
        if final == source:
            continue
        repo2 = isolate(REPO, native / "confirm" / name, name)
        att = score_logged(repo2 / "nonmatchings" / name, repo2, name, final, conn=conn,
            strategy="repair-mechanisms:mmio-confirm", run_id="repair-mechanisms-mmio-confirm:" + name,
            parent_attempt_id=rows[-1]["receipt_id"], action="independent-confirmation",
            model="independent-compiler", prompt="Fresh workspace confirmation of generated MMIO candidate.",
            extra={"training_eligible": False, "assistance_tier": "source-independent"})
        (out / f"{name}--confirmed.json").write_text(json.dumps(_attempt_to_verdict(att), indent=2))
        boundary = (att.verification or {}).get("function_boundary") or {}
        confirmation = {"function": name, "receipt_id": att.receipt_id, "source_sha256": sha(final),
            "object_exact": att.exact, "function_exact": boundary.get("function_exact"),
            "boundary_schema": boundary.get("schema_version"), "frontend_passed": (att.frontend or {}).get("passed")}
        report["confirmations"].append(confirmation)
        save()
        assert confirmation["frontend_passed"] and (att.exact or (confirmation["function_exact"] and confirmation["boundary_schema"] == 3)), confirmation
        print(json.dumps(confirmation), flush=True)
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == digest for p, digest in report["implementation_sha256"].items())
    report["complete"] = True
    report["total_compiles"] = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
    save()
    conn.close()
    upstream.close()


if __name__ == "__main__":
    main()

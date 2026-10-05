"""Exercise the ordinary registered tool on both storage repair motivations."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

OUT = Path(__file__).resolve().parent
FROZEN = json.loads((OUT / "freeze-v3.json").read_text())
CODE = Path(FROZEN["code_root"])
sys.path.insert(0, str(CODE))
from eval.campaign_workers import isolate
from eval.search_evolution import compile_logged
from eval.search_replay import digest
from eval.tool_agent import Context, ScriptedPolicy, run_episode
from solver import regalloc_mutations

REPO = Path.home() / "decomp/sbk1"
NATIVE = Path.home() / "decomp/experiments/register-storage-20260922/public-v3"


def main():
    NATIVE.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(NATIVE / "attempts.sqlite")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()
    before = {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    original = regalloc_mutations.variants
    results = {"complete": False, "training_eligible": False, "database": str(NATIVE / "attempts.sqlite"),
               "fixed_files": FROZEN["files"], "code_root": str(CODE), "runs": [], "confirmations": []}

    def check():
        for p, sha in FROZEN["files"].items():
            assert hashlib.sha256((CODE / p).read_bytes()).hexdigest() == sha

    for name, source_file in (("__osDequeueThread", "probe-baseline.c"), ("osGetThreadPri", "parameter-baseline.c")):
        source = (OUT / source_file).read_text()
        repo = isolate(REPO, NATIVE / name / "public", name)
        ws = repo / "nonmatchings" / name
        rows, receipts, parents = [], {}, {}

        def variants(code, function, diff="", prefer=()):
            for label, kind, child in original(code, function, diff, prefer):
                parents[digest(child)] = receipts[digest(code)]
                yield label, kind, child

        def compile_one(code):
            check()
            assert len(rows) < 34
            parent = parents.get(digest(code), receipts.get(digest(code)))
            verdict = compile_logged(ws, repo, name, code, conn=db,
                strategy="storage-repair-public-v3", run_id=f"storage-public-v3:{name}",
                parent_attempt_id=parent, action="regalloc-search", model="deterministic-tool-controller",
                extra={"training_eligible": False, "assistance_tier": "header-assisted"})
            assert not verdict.get("error")
            dump = ws / f"{name}_object_dump_normalized.s"
            verdict["dump"] = dump.read_text() if verdict["compiled"] and dump.exists() else ""
            receipts[digest(code)] = verdict["receipt_id"]
            rows.append({"parent_receipt_id": parent, "candidate_sha256": digest(code), "verdict": verdict})
            return verdict

        try:
            regalloc_mutations.variants = variants
            base = compile_one(source)
            context = Context(function=name, candidate=source, compile_fn=compile_one,
                target_dump=(ws / "target_object_dump_normalized.s").read_text(), diff=base["diff"], initial_verdict=base)
            emitted = []
            trace = run_episode(context, ScriptedPolicy(order=[("regalloc-search", {"budget": 32, "beam": 3})]),
                                budget=1, on_candidate=emitted.append)
        finally:
            regalloc_mutations.variants = original
        assert not trace.exact or emitted[-1] == context.candidate
        saved = context.candidate
        (OUT / f"public--{name}.c").write_text(saved)
        result = {"function": name, "exact": trace.exact, "compiles": len(rows), "source_sha256": digest(saved),
                  "attempts": rows, "transcript": trace.as_dict()}
        results["runs"].append(result)
        if trace.exact:
            assert context.initial_verdict["source_sha256"] == digest(saved)
            confirm_repo = isolate(REPO, NATIVE / name / "confirmation", name)
            parent = receipts[digest(saved)]
            verdict = compile_logged(confirm_repo / "nonmatchings" / name, confirm_repo, name, saved, conn=db,
                strategy="storage-public-confirmation", run_id=f"storage-public-confirm:{name}",
                parent_attempt_id=parent, action="independent-confirmation", model="independent-compiler",
                extra={"training_eligible": False, "assistance_tier": "header-assisted"})
            assert verdict["exact"] and not verdict.get("error")
            results["confirmations"].append({"function": name, "candidate_sha256": digest(saved),
                                             "parent_receipt_id": parent, "verdict": verdict})
        (OUT / "public-verification.json").write_text(json.dumps(results, indent=2))
        print(json.dumps({"function": name, "exact": trace.exact, "compiles": len(rows)}), flush=True)
    check()
    assert before == {r[0] for r in upstream.execute("SELECT DISTINCT func_addr FROM attempts WHERE exact=1")}
    results["total_compiles"] = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
    assert results["total_compiles"] == sum(r["compiles"] for r in results["runs"]) + len(results["confirmations"])
    results["complete"] = True
    (OUT / "public-verification.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

"""Freeze code and the WHOLE compiling-but-not-exact population before any compile.

Why this experiment exists. Every transfer test of the 2026-09-22 repair families used a panel of
4-8 names chosen by module family (thread functions, timer functions), and most of those names
either lacked a workspace or failed to compile, so no family was ever measured on the residuals it
targets. Applicability is a property of the residual, not of the module a function lives in. The
denominator here is every function with a compiling attempt and no exact attempt, selected from
the main KB read-only, with its best compiling source frozen by hash before anything compiles.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

OUT = Path(__file__).resolve().parent
SHARED = OUT.parents[2]
BASE = Path.home() / "decomp/experiments/generated-potential-20260922/code-v1"
DEST = Path.home() / "decomp/experiments/population-transfer-20260922/code-v1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
NEW = ["register_storage", "address_reuse", "parameter_reuse", "unsigned_float",
       "cursor_rebase", "residual_evidence", "cursor_advance"]
CHECK = ["solver/storage_repairs.py", "solver/representation_repairs.py", "solver/cursor_advance.py",
         "solver/regalloc_mutations.py", "solver/workspace.py", "eval/search_replay.py",
         "eval/search_scheduler.py", "eval/search_evolution.py"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main():
    assert not DEST.exists() and not (OUT / "freeze.json").exists()
    for relative in CHECK:                       # the snapshot must be the code on the Windows side
        assert sha((BASE / relative).read_bytes()) == sha((SHARED / relative).read_bytes()), relative
    shutil.copytree(BASE, DEST, ignore=shutil.ignore_patterns("__pycache__"))
    kb = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    rows = kb.execute(
        "select f.name, f.addr, f.insn_count from functions f where exists "
        "(select 1 from attempts a where a.func_addr = f.addr and a.compiled = 1) "
        "and not exists (select 1 from attempts a where a.func_addr = f.addr and a.exact = 1) "
        "order by f.name").fetchall()
    population = []
    for name, addr, insns in rows:
        attempt_id, source, score = kb.execute(
            "select id, source_code, score from attempts where func_addr = ? and compiled = 1 "
            "and source_code is not null order by score desc, id asc limit 1", (addr,)).fetchone()
        population.append({"function": name, "addr": addr, "insn_count": insns,
                           "source_attempt_id": attempt_id, "source_score": score,
                           "source_sha256": sha(source.encode()), "source": source})
    exact_before = sorted(r[0] for r in kb.execute(
        "select distinct f.name from attempts a join functions f on f.addr = a.func_addr where a.exact = 1"))
    (OUT / "population.json").write_text(json.dumps(population, indent=1))
    fixed = sorted((DEST / "solver").glob("*.py")) + sorted((DEST / "eval").glob("*.py"))
    fixed += sorted((DEST / "kb").glob("*.py")) + [DEST / "kb/schema.sql",
                                                    DEST / "eval/results/dream-search-20260922/pilot.py"]
    manifest = {"base": str(BASE), "code_root": str(DEST), "kb": str(KB),
                "files": {str(p.relative_to(DEST)): sha(p.read_bytes()) for p in fixed},
                "population_sha256": sha((OUT / "population.json").read_bytes()),
                "population": len(population), "exact_before": len(exact_before),
                "exact_before_sha256": sha(json.dumps(exact_before).encode()),
                "new_families": NEW, "budget_per_arm": 32, "max_depth": 4,
                "scheduler": {"name": "depth-1.18754", "mode": "depth", "quantum": 1.18754},
                "arms": {"control": "variants() minus the seven 2026-09-22 families",
                         "expanded": "variants() unchanged"},
                "nproc": int(subprocess.check_output(["nproc"], text=True)),
                "training_eligible": False, "reference_body_supplied": False, "model_calls": 0,
                "selection": "every function with a compiling attempt and no exact attempt; "
                             "best-scoring compiling source; frozen before any compile; all outcomes kept"}
    (OUT / "freeze.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"population": len(population), "fixed_files": len(fixed),
                      "exact_before": len(exact_before), "nproc": manifest["nproc"]}))


if __name__ == "__main__":
    main()

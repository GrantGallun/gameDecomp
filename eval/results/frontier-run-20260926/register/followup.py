"""Run only the predeclared lifetime/zero experiments with ordinary private receipts."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import workspace

NAME = "releaseSoundEffectHandleNode"
PRIVATE = Path("/home/grant/decomp/experiments/frontier-register-20260926")
REPO = PRIVATE / "repo"
WS = REPO / "nonmatchings" / NAME
MAIN_DB = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
DB = PRIVATE / "followup.sqlite"
PARENT_SHA = "f880a1239cd9b4cb3f75ef64a0c28dea4f664ad184294112738e5945fda70392"
OLD = "            gActiveSoundHandleListTail = temp_v0;"
EDITS = (
    ("conditional_null", "            gActiveSoundHandleListTail = temp_v0 ? NULL : temp_v0;"),
    ("self_xor", "            gActiveSoundHandleListTail = (void *)((u32)temp_v0 ^ (u32)temp_v0);"),
    ("copy_then_zero", "            gActiveSoundHandleListTail = temp_v0;\n            gActiveSoundHandleListTail = NULL;"),
)


def setup_db():
    if DB.exists():
        raise RuntimeError("followup database already exists; refusing duplicate compiles")
    db = sqlite3.connect(DB)
    schema = Path("/mnt/c/Code/gameDecomp/kb/schema.sql").read_text()
    db.executescript(schema)
    upstream = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    upstream.row_factory = sqlite3.Row
    fn = upstream.execute("select * from functions where name=?", (NAME,)).fetchone()
    assert fn is not None
    tu = upstream.execute("select * from tus where id=?", (fn["tu_id"],)).fetchone()
    assert tu is not None
    for table, row in (("tus", tu), ("functions", fn)):
        cols = list(row.keys())
        db.execute(f"insert into {table} ({','.join(cols)}) values ({','.join('?' for _ in cols)})",
                   tuple(row[c] for c in cols))
    db.commit()
    return db


def verdict(attempt, label, source, dump):
    lines = dump.splitlines() if dump else []
    window = lines[7:14] if len(lines) >= 14 else []
    return {"label": label, "receipt_id": attempt.receipt_id,
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
            "compiled": attempt.compiled, "score": attempt.score, "exact": attempt.exact,
            "frontend_passed": (attempt.frontend or {}).get("passed"),
            "certificate_exact": (attempt.verification or {}).get("exact"),
            "first_arm_window": window, "diff": attempt.diff, "error": attempt.compiler_stderr,
            "verification_path": str(WS / f"{NAME}_followup_{label}.verification.json")}


def main():
    parent = (PRIVATE / "probe.c").read_text()
    assert hashlib.sha256(parent.encode()).hexdigest() == PARENT_SHA
    assert parent.count(OLD) == 1
    db = setup_db()
    results = []
    run_id = "frontier-register-20260926:releaseSoundEffectHandleNode"
    for index, (label, source, parent_id) in enumerate(
        [("parent", parent, None)] + [(label, parent.replace(OLD, new), "parent") for label, new in EDITS]
    ):
        (PRIVATE / f"followup-{label}.c").write_text(source)
        tag = f"{NAME}_followup_{label}"
        attempt = workspace.score(
            WS, REPO, tag, source, conn=db, func=NAME,
            strategy=f"frontier-register-lifetime:{label}", model="deterministic-private-probe",
            prompt="Retained candidate, target assembly, uopt trace and predeclared FOLLOWUP_PROTOCOL.md only.",
            run_id=run_id, parent_attempt_id=results[0]["receipt_id"] if parent_id else None,
            relation="derive" if parent_id else "", action=label,
            extra={"training_eligible": False, "parent_campaign_attempt": 108368,
                   "protocol": "FOLLOWUP_PROTOCOL.md"}, iteration=index)
        path = WS / f"{tag}_object_dump_normalized.s"
        dump = path.read_text() if attempt.compiled and path.exists() else None
        if dump:
            (PRIVATE / f"followup-{label}.s").write_text(dump)
        row = verdict(attempt, label, source, dump)
        results.append(row)
        (PRIVATE / "followup-results.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(row, indent=2), flush=True)
        if label != "parent" and attempt.exact and (attempt.frontend or {}).get("passed") is True:
            break


if __name__ == "__main__":
    main()

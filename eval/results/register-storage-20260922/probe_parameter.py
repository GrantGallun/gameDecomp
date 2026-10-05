"""Causal development probe after the first frozen transfer comparison."""
import json
import re
import sqlite3
from probe import CODE, SHARED, REPO, isolate, compile_logged
from pathlib import Path


def main():
    native = Path.home() / "decomp/experiments/register-storage-20260922/parameter-probe"
    native.mkdir(parents=True, exist_ok=False)
    name = "osGetThreadPri"
    original = (SHARED / "inputs" / f"{name}.c").read_text()
    candidate = original.replace("    OSThread *var_a0;\n", "").replace("    var_a0 = arg0;\n", "")
    candidate = re.sub(r"\bvar_a0\b", "arg0", candidate)
    db = sqlite3.connect(native / "attempts.sqlite")
    db.executescript((CODE / "kb/schema.sql").read_text())
    upstream = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    for table in ("tus", "functions"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
        db.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                       upstream.execute(f"SELECT {','.join(cols)} FROM {table}"))
    db.commit()
    rows = []
    for label, source in [("baseline", original), ("parameter-reuse", candidate), ("confirmation", candidate)]:
        repo = isolate(REPO, native / label, name)
        verdict = compile_logged(repo / "nonmatchings" / name, repo, name, source, conn=db,
            strategy=f"parameter-reuse-probe:{label}", run_id="parameter-reuse-probe",
            parent_attempt_id=rows[-1]["receipt_id"] if rows else None, action=label, model="deterministic-probe",
            extra={"training_eligible": False, "assistance_tier": "header-assisted"})
        assert not verdict.get("error")
        rows.append({"label": label, **verdict})
        (SHARED / f"parameter-{label}.c").write_text(source)
        print(json.dumps({"label": label, "score": verdict["score"], "exact": verdict["exact"]}), flush=True)
    (SHARED / "parameter-probe.json").write_text(json.dumps({"database": str(native / "attempts.sqlite"), "runs": rows}, indent=2))


if __name__ == "__main__":
    main()

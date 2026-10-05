"""Test predeclared shared-local web hypothesis using private ordinary scoring."""
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
DB = PRIVATE / "followup.sqlite"
MAIN_DB = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
PARENT_SHA = "a393535d050115637b0582fceb7ff52c8c1efa34c3850dcafce3bec6d257f43f"


def run(db, label, source, parent_id):
    (PRIVATE / f"web-{label}.c").write_text(source)
    tag = f"{NAME}_web_{label}"
    attempt = workspace.score(
        WS, REPO, tag, source, conn=db, func=NAME,
        strategy=f"frontier-register-web:{label}", model="deterministic-private-probe",
        prompt="Retained candidate, target assembly, uopt trace and predeclared WEB_MERGE_PROTOCOL.md only.",
        run_id="frontier-register-20260926:web-merge", parent_attempt_id=parent_id,
        relation="derive" if parent_id else "", action=label,
        extra={"training_eligible": False, "parent_campaign_attempt": 108368,
               "protocol": "WEB_MERGE_PROTOCOL.md"})
    dump_path = WS / f"{tag}_object_dump_normalized.s"
    dump = dump_path.read_text() if attempt.compiled and dump_path.exists() else None
    if dump:
        (PRIVATE / f"web-{label}.s").write_text(dump)
    result = {"label": label, "receipt_id": attempt.receipt_id,
              "sha256": hashlib.sha256(source.encode()).hexdigest(), "compiled": attempt.compiled,
              "score": attempt.score, "exact": attempt.exact,
              "frontend_passed": (attempt.frontend or {}).get("passed"),
              "certificate_exact": (attempt.verification or {}).get("exact"),
              "first_arm_window": dump.splitlines()[7:14] if dump else [],
              "diff": attempt.diff, "error": attempt.compiler_stderr,
              "verification_path": str(WS / f"{tag}.verification.json")}
    print(json.dumps(result, indent=2), flush=True)
    return result


def main():
    upstream = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    parent = upstream.execute("select source_code from attempts where id=108368").fetchone()[0]
    assert hashlib.sha256(parent.encode()).hexdigest() == PARENT_SHA
    assert parent.count("temp_v1_2") == 4
    child = parent.replace("    void **temp_v1_2;\n", "").replace("temp_v1_2", "temp_v1")
    assert child.count("temp_v1_2") == 0
    db = sqlite3.connect(DB)
    prior = db.execute("select count(*) from attempts where strategy like 'frontier-register-web:%'").fetchone()[0]
    assert prior == 0, "refusing duplicate web compiles"
    root = run(db, "retained_parent", parent, None)
    child_result = run(db, "shared_local", child, root["receipt_id"])
    (PRIVATE / "web-results.json").write_text(json.dumps([root, child_result], indent=2))


if __name__ == "__main__":
    main()

"""Privately score the local-web generator on its retained motivating attempt.

This reads only campaign attempt 108368 as candidate input. It does not read
ground-truth or winning source. Run inside the configured WSL environment.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import local_web_merge, workspace


NAME = "releaseSoundEffectHandleNode"
PRIVATE = Path("/home/grant/decomp/experiments/frontier-register-20260926")
REPO = PRIVATE / "repo"
WS = REPO / "nonmatchings" / NAME
MAIN_DB = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
DB_PATH = PRIVATE / "web-generator.sqlite"
RESULT_PATH = Path(__file__).with_name("result.json")
PARENT_ATTEMPT = 108368
PARENT_SHA = "a393535d050115637b0582fceb7ff52c8c1efa34c3850dcafce3bec6d257f43f"
CHILD_SHA = "6a1b329a5be400cc790296112c8757b097761fc1e642f68b4e9e9241fdd82af5"


def main():
    campaign = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    row = campaign.execute("select source_code from attempts where id=?", (PARENT_ATTEMPT,)).fetchone()
    if row is None:
        raise SystemExit(f"retained campaign attempt {PARENT_ATTEMPT} is absent")
    parent = row[0]
    if hashlib.sha256(parent.encode()).hexdigest() != PARENT_SHA:
        raise SystemExit("retained candidate source hash does not match the protocol")

    candidates = [item for item in local_web_merge.variants(parent, NAME)
                  if item[1] == "local_web_merge"]
    expected = [item for item in candidates if hashlib.sha256(item[2].encode()).hexdigest() == CHILD_SHA]
    if len(expected) != 1:
        raise SystemExit(f"expected one generated protocol child, found {len(expected)}")
    _label, _family, child = expected[0]

    db = sqlite3.connect(DB_PATH)
    existing = db.execute(
        "select count(*) from attempts where strategy like 'frontier-register-web-generator:%'"
    ).fetchone()[0]
    if existing:
        raise SystemExit(f"refusing duplicate scoring run: found {existing} existing attempts")

    def score(label, source, parent_id=None):
        tag = f"{NAME}_webgen_{label}"
        attempt = workspace.score(
            WS, REPO, tag, source, conn=db, func=NAME,
            strategy=f"frontier-register-web-generator:{label}",
            model="deterministic-private-probe",
            prompt="Retained candidate, target assembly and WEB_MERGE_PROTOCOL.md only.",
            run_id="frontier-register-20260926:web-generator",
            parent_attempt_id=parent_id, relation="derive" if parent_id else "",
            action=label,
            extra={"training_eligible": False, "parent_campaign_attempt": PARENT_ATTEMPT,
                   "generator": "solver.local_web_merge"},
        )
        return {
            "label": label,
            "receipt_id": attempt.receipt_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "compiled": attempt.compiled,
            "score": attempt.score,
            "exact": attempt.exact,
            "frontend_passed": (attempt.frontend or {}).get("passed"),
            "certificate_exact": (attempt.verification or {}).get("exact"),
            "diff": attempt.diff,
            "error": attempt.compiler_stderr,
        }

    baseline = score("parent", parent)
    result = score("shared_local", child, baseline["receipt_id"])
    output = {"parent_campaign_attempt": PARENT_ATTEMPT, "generated_label": expected[0][0],
              "parent": baseline, "candidate": result}
    RESULT_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output, indent=2), flush=True)


if __name__ == "__main__":
    main()

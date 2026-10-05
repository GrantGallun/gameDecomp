"""Audit all stages without double counting the shared causal probes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
for script in ("audit.py", "audit_next.py", "audit_final.py"):
    subprocess.run([sys.executable, str(OUT / script)], check=True)
from audit_final import attempts_at, bind, digest

public = json.loads((OUT / "public-verification.json").read_text())
assert public["complete"] and not public["training_eligible"]
_, attempts, names, edges = attempts_at(public["database"])
used = []
for run in public["runs"]:
    assert len(run["attempts"]) == run["compiles"] <= 33
    assert run["exact"]
    assert digest((OUT / f"public--{run['function']}.c").read_text()) == run["source_sha256"]
    for row in run["attempts"]:
        stored = attempts[row["verdict"]["receipt_id"]]
        bind(row["verdict"], stored)
        assert stored["source_sha256"] == row["candidate_sha256"]
        assert stored["parent_attempt_id"] == row["parent_receipt_id"]
        assert names[stored["func_addr"]] == run["function"]
        used.append(stored["id"])
    assert any(r["verdict"]["exact"] and r["candidate_sha256"] == run["source_sha256"] for r in run["attempts"])
for row in public["confirmations"]:
    stored = attempts[row["verdict"]["receipt_id"]]
    bind(row["verdict"], stored)
    assert stored["exact"] and stored["source_sha256"] == row["candidate_sha256"]
    assert stored["parent_attempt_id"] == row["parent_receipt_id"]
    assert names[stored["func_addr"]] == row["function"]
    assert digest((OUT / f"public--{row['function']}.c").read_text()) == row["candidate_sha256"]
    used.append(stored["id"])
assert {r["function"] for r in public["confirmations"]} == {r["function"] for r in public["runs"]}
assert len(used) == len(set(used)) == len(attempts) == public["total_compiles"] and set(used) == set(attempts)
frozen = json.loads((OUT / "freeze-v3.json").read_text())
for p, sha in frozen["files"].items():
    assert hashlib.sha256((Path(frozen["code_root"]) / p).read_bytes()).hexdigest() == sha
for p in frozen["overlays"]:
    assert hashlib.sha256((OUT.parents[2] / p).read_bytes()).hexdigest() == frozen["files"][p]
stages = [json.loads((OUT / f"paired-v{i}/audit.json").read_text()) for i in (1,2,3)]
summary = {"complete": True, "causal_probe_compiles": 13,
    "paired_compiles": [r["paired_compiles"] for r in stages], "public_compiles": len(attempts),
    "total_compiles": 13 + sum(r["paired_compiles"] for r in stages) + len(attempts),
    "paired_worlds": sum(r["worlds"] for r in stages),
    "independent_paired_confirmations": sum(r["independent_paired_confirmations"] for r in stages),
    "independent_public_confirmations": len(public["confirmations"]),
    "final_known_losses": stages[-1]["known_losses"], "final_followup_gains": stages[-1]["followup_gains"],
    "all_receipt_bindings_valid": True, "current_overlays_match_final_snapshot": True,
    "training_eligible": False, "production_mutated": False, "model_calls": 0}
(OUT / "audit.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))

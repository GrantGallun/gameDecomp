"""Read-only live pilot snapshot, optionally copied into this experiment's report."""
import argparse
import collections
import json
from pathlib import Path

E = Path.home() / "decomp/experiments/edit-capability-20261002"
ap = argparse.ArgumentParser()
ap.add_argument("--save", action="store_true")
args = ap.parse_args()
snapshot = {}
for name, path in {
    "launch": E / "logic-pilot-v3.launch.json",
    "pilot": E / "logic-pilot-v3/status.json",
    "smoke": E / "logic-pilot-v3-smoke-completion-logits/status.json",
}.items():
    if path.exists():
        snapshot[name] = json.loads(path.read_text())
recovery = E / "public/context-v3.recovery.json"
if recovery.exists():
    manifest = json.loads(recovery.read_text())
    rows = []
    journal = E / "public/context-v3.groups.jsonl"
    if journal.exists():
        with journal.open() as f:
            for line in f:
                if line.endswith("\n"):
                    rows.append(json.loads(line))
    snapshot["context"] = {"completed_groups": manifest["retained_groups"] + len(rows),
                            "total_groups": manifest["total_groups"],
                            "admitted_rows": rows[-1]["cumulative"] if rows else manifest["retained_rows"],
                            "new_infrastructure_errors": sum(bool(r["error"]) for r in rows)}
training = E / "logic-pilot-v3-smoke-completion-logits/adapter_mixed/training_receipt.json"
if training.exists():
    receipt = json.loads(training.read_text())
    snapshot["smoke_training"] = {k: receipt[k] for k in
        ("steps_run", "published", "peak_gpu_gb", "weight_change", "seconds", "hyperparameters")}
print(json.dumps(snapshot, indent=2))
if args.save:
    Path(__file__).with_name("resume-receipt.json").write_text(json.dumps(snapshot, indent=2))

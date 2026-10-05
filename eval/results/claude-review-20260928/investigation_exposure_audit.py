"""Read-only audit of saved added-case exposure; no solver execution or source reads.

Run under WSL with --native-root, --receipts, --live-pointer, and --output.
Only --output is written. SQLite is opened with mode=ro and query_only.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected(row, names):
    return {name: row.get(name) for name in names}


def base_view(row):
    return selected(row, ("status", "counts", "total", "source_sha256", "panel_sha256", "semantic_key"))


def extra_view(path):
    row = read(path)
    return {"path": str(path), "sha256": sha(path),
            **selected(row, ("status", "comparison", "reason", "source_sha256", "panel_sha256")),
            "target_status": (row.get("target") or {}).get("status"),
            "candidate_status": (row.get("candidate") or {}).get("status"),
            "replay_cases": len(row.get("cases", [])),
            "replay_comparisons": dict(Counter(c.get("comparison") for c in row.get("cases", [])))}


def live_metadata(pointer_path):
    pointer = read(pointer_path)
    uri = pointer_path.with_name(pointer["store"]).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        raw = conn.execute("SELECT manifest FROM commits WHERE id=?", (pointer["commit"],)).fetchone()[0]
        assert hashlib.sha256(raw).hexdigest() == pointer["sha256"]
        manifest = json.loads(raw)
        blob = conn.execute("SELECT payload FROM objects WHERE hash=?", (manifest["metadata"],)).fetchone()[0]
        raw_metadata = zlib.decompress(blob)
        assert hashlib.sha256(raw_metadata).hexdigest() == manifest["metadata"]
        metadata = json.loads(raw_metadata)
    config = metadata["config"]
    project = Path(config["project"])
    code = {}
    for name in ("solver/investigation.py", "solver/toolagent.py", "solver/execution_experiment.py", "eval/agentrepair.py"):
        path = project / name
        text = path.read_text() if path.exists() else ""
        actual_hash = sha(path) if path.exists() else None
        pinned_hash = metadata.get("pins", {}).get(str(path))
        code[name] = {"exists": path.exists(), "sha256": sha(path) if path.exists() else None,
                      "pinned_sha256": pinned_hash,
                      "matches_pin": actual_hash == pinned_hash if pinned_hash else None,
                      "mentions_execution_case": "execution_case" in text,
                      "mentions_execution_experiment": "execution_experiment" in text}
    return {"pointer": str(pointer_path), "pointer_sha256": sha(pointer_path),
            **selected(pointer, ("commit", "updated_at", "status")),
            "metadata_sha256": manifest["metadata"],
            "config": selected(config, ("scheduler", "investigation_policy", "model_calls", "project", "repo")),
            "inflight_profiles": [j.get("profile") for j in metadata.get("fast_inflight", [])],
            "frozen_code": code,
            "scope": "Current checkpoint configuration and pinned code only; historical main-ledger exposure not counted."}


def main():
    parser = argparse.ArgumentParser()
    for name in ("native-root", "receipts", "live-pointer", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.native_root
    revisions = [root, *sorted(p for p in root.glob("v*") if p.is_dir())]
    runs, inventory = [], []
    actions, statuses = Counter(), Counter()
    for folder in revisions:
        revision = "initial" if folder == root else folder.name
        for path in sorted((folder / "model").glob("*/result.repair.json")):
            data = read(path)
            investigation = data.get("investigation") or {}
            events = investigation.get("events", [])
            observations = investigation.get("observations", [])
            function = data["config"]["function"]
            local = args.receipts / revision / function / "events.json"
            packaged = read(local)
            assert packaged["events"] == events, str(local)
            actions.update(e.get("action", "generation") for e in events)
            statuses.update(e.get("status") for e in events)
            execution = [extra_view(p) for p in sorted(path.parent.glob("*-investigation/execution/*.json"))]
            inventory.extend(execution)
            runs.append({"revision": revision, "function": function,
                         "native_path": str(path), "native_sha256": sha(path),
                         "packaged_path": str(local), "packaged_sha256": sha(local),
                         "packaged_events_equal_native": True, "events": len(events),
                         "calls_attempted": data.get("result", {}).get("calls_attempted"),
                         "execution_actions": sum(e.get("action") == "execution_case" for e in events),
                         "execution_observation_statuses": [o.get("status") for o in observations if o.get("kind") == "execution-experiment"],
                         "final_semantic": base_view(data.get("result", {}).get("semantic_validation") or {}),
                         "execution_receipts": execution})
    smoke_path = root / "mechanism-smoke.json"
    assert read(smoke_path) == read(args.receipts / "mechanism-smoke.json")
    smoke = []
    for row in read(smoke_path):
        base, extra = row.get("semantic") or {}, row.get("execution") or {}
        paired = all(base.get(k) and base.get(k) == extra.get(k) for k in ("source_sha256", "panel_sha256"))
        smoke.append({"function": row["function"], "base": base_view(base),
                      "extra": extra_view(Path(extra["receipt"])), "source_and_panel_identity_equal": paired,
                      "base_pass_and_extra_completed_comparison": paired and base.get("status") == "observed_pass_with_execution_debt" and extra.get("comparison") in ("passed", "failed")})
    manual_receipts = [extra_view(p) for p in sorted(root.glob("smoke-execution-*/*.json"))]
    all_autonomous_paths = sorted(p for folder in revisions for p in (folder / "model").glob("*/*-investigation/execution/*.json"))
    assert {str(p) for p in all_autonomous_paths} == {r["path"] for r in inventory}, "Orphan execution receipt needs examination"
    comparison_rows = [row for row in smoke if row["base_pass_and_extra_completed_comparison"]]
    result = {
        "audit_time_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Parse saved receipts only; do not rerun target/candidate execution. Separate autonomous canaries from manually forced controls. Join base and extra outcomes only with equal source and panel hashes.",
        "native_root": str(root), "packaged_receipts": str(args.receipts),
        "autonomous": {"runs": len(runs), "functions": len({r["function"] for r in runs}),
                       "event_count": sum(actions.values()), "actions": dict(actions), "event_statuses": dict(statuses),
                       "execution_actions": actions["execution_case"],
                       "execution_receipt_count": len(inventory),
                       "execution_statuses": dict(Counter(r["status"] for r in inventory)),
                       "replay_receipts": sum(Path(r["path"]).name.startswith("replay-") for r in inventory),
                       "completed_extra_comparisons": sum(r["comparison"] in ("passed", "failed") for r in inventory),
                       "base_pass_candidates_with_completed_extra_comparisons": 0 if not any(r["comparison"] in ("passed", "failed") or r["replay_cases"] for r in inventory) else None,
                       "base_pass_candidates_with_extra_replays": 0 if not any(r["replay_cases"] for r in inventory) else None,
                       "observed_base_pass_extra_fail": 0 if not any(r["comparison"] in ("passed", "failed") or r["replay_cases"] for r in inventory) else None,
                       "contradiction_frequency": None,
                       "frequency_note": "No exposed candidate denominator; zero observed contradictions is not a measured zero failure rate.",
                       "run_rows": runs},
        "manual_controls": {"report_path": str(smoke_path), "report_sha256": sha(smoke_path),
                            "proposal_receipts": len(manual_receipts), "rows": smoke,
                            "base_pass_completed_extra_comparison_denominator": len(comparison_rows),
                            "base_pass_extra_fail": sum(r["extra"]["comparison"] == "failed" for r in comparison_rows),
                            "contradiction_frequency": None if not comparison_rows else sum(r["extra"]["comparison"] == "failed" for r in comparison_rows) / len(comparison_rows)},
        "current_live_campaign": live_metadata(args.live_pointer),
        "limits": ["This audit does not establish a contradiction frequency for untested candidates.",
                   "An unavailable panel or target-inconclusive case is not a passing extra case.",
                   "No historical main campaign DB scan; current deployment is established from checkpoint metadata and code.",
                   "Saved canary results are assisted development observations, not held-out generalization evidence."]}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"autonomous": {k:v for k,v in result["autonomous"].items() if k != "run_rows"},
                      "manual_controls": smoke, "current_live_campaign": result["current_live_campaign"],
                      "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()

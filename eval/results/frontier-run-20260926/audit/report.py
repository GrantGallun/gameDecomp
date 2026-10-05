"""Read-only frontier receipt report; safe to run while a batch is active.

Run from either Windows or WSL: python eval/results/frontier-run-20260926/audit/report.py
Use --json for machine-readable output. Only baseline, progress, batch receipts,
and controller logs in the parent directory are read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


RUN = Path(__file__).resolve().parent.parent
EXACT = {"object_exact", "integrated", "function_exact_pending_integration"}


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def log_rows(path: Path):
    rows, other = [], []
    if not path.exists():
        return rows, other
    # A running writer may leave a partial final line. Discard only that line.
    data = path.read_bytes().splitlines(keepends=True)
    for raw in data:
        if not raw.endswith(b"\n"):
            continue
        line = raw.decode("utf-8", "replace").strip()
        try:
            item = json.loads(line)
        except ValueError:
            other.append(line)
            continue
        if isinstance(item, dict) and "function" in item and "profile" in item:
            rows.append(item)
        else:
            other.append(line)
    return rows, other


def exact_names(nodes):
    return {name for name, row in nodes.items() if row["status"] in EXACT}


def add_profiles(target, rows):
    for row in rows:
        name = row["profile"].split("@", 1)[0]
        perf = row.get("performance") or {}
        bucket = target[name]
        bucket["work_items"] += 1
        bucket["compile_misses"] += perf.get("compile_misses", 0)
        bucket["compile_hits"] += perf.get("compile_hits", 0)
        bucket["compile_seconds"] += perf.get("compile_seconds", 0)
        bucket["model_calls"] += perf.get("model_calls", 0)
        bucket["parked_rows"] += row.get("status") == "parked"


def report(run: Path):
    baseline = read_json(run / "baseline.json")
    if baseline is None:
        raise SystemExit(f"Missing or incomplete baseline: {run / 'baseline.json'}")
    progress = read_json(run / "progress.json") or {}
    initial = baseline["nodes"]
    current = dict(initial)
    research = set(baseline["research_exact"])
    campaign = set(baseline["campaign_attempt_exact"])
    profiles = defaultdict(Counter)
    batches, active, discrepancies, parked = [], None, [], []
    previous = baseline
    for directory in sorted(run.glob("batch-[0-9][0-9][0-9][0-9]")):
        receipt = read_json(directory / "canary.json")
        rows, other = log_rows(directory / "canary-controller.log")
        if receipt is None:
            active = {"batch": directory.name, "completed_log_rows": len(rows),
                      "profiles": summarize_profiles(rows), "non_json_log_lines": other[-10:]}
            continue
        add_profiles(profiles, rows)
        changed = receipt.get("changed_nodes", {})
        for name, pair in changed.items():
            if name in current and current[name] != pair["before"]:
                discrepancies.append(f"{directory.name}: before node differs from previous receipt: {name}")
            current[name] = pair["after"]
            if pair["after"]["status"] == "parked" and pair["before"]["status"] != "parked":
                parked.append({"batch": directory.name, "function": name,
                               "last_profile": pair["after"].get("last_profile"),
                               "blocker": "not included in canary receipt"})
        session = receipt.get("last_session") or {}
        batch = {"batch": directory.name,
                 "work_items": session.get("completed_items"),
                 "log_rows": len(rows),
                 "new_attempts": receipt["after"]["attempts"] - receipt["before"]["attempts"],
                 "wall_seconds": receipt["finished_at"] - receipt["started_at"],
                 "gained_exact_names": receipt.get("gained_exact_names", []),
                 "lost_exact_names": receipt.get("lost_exact_names", []),
                 "returncode": receipt.get("returncode"), "timed_out": receipt.get("timed_out"),
                 "stopped_reason": session.get("stopped_reason"),
                 "model_call_delta": receipt["after"]["model_calls"] - receipt["before"]["model_calls"],
                 "model_proposal_delta": receipt["after"]["model_proposals"] - receipt["before"]["model_proposals"],
                 "non_json_log_lines": other[-10:]}
        batches.append(batch)
        if batch["work_items"] != batch["log_rows"]:
            discrepancies.append(f"{directory.name}: session work {batch['work_items']} != log rows {batch['log_rows']}")
        if receipt["before"]["commit"] != previous["commit"]:
            discrepancies.append(f"{directory.name}: checkpoint chain break")
        direct_gains = {n for n, pair in changed.items()
                        if pair["before"]["status"] not in EXACT and pair["after"]["status"] in EXACT}
        direct_losses = {n for n, pair in changed.items()
                         if pair["before"]["status"] in EXACT and pair["after"]["status"] not in EXACT}
        if direct_gains != set(batch["gained_exact_names"]) or direct_losses != set(batch["lost_exact_names"]):
            discrepancies.append(f"{directory.name}: exact membership differs from receipt names")
        if batch["returncode"] or batch["timed_out"] or direct_losses or batch["model_call_delta"] or batch["model_proposal_delta"]:
            discrepancies.append(f"{directory.name}: controller/ratchet/model safety condition")
        if receipt.get("control_pause_preserved") is not True or receipt.get("native_pause_restored") is not True:
            discrepancies.append(f"{directory.name}: durable pause marker missing")
        if any(pair["after"]["status"] == "parked" and pair["before"]["status"] != "parked"
               for pair in changed.values()):
            discrepancies.append(f"{directory.name}: newly parked node; blocker unavailable in canary receipt")
        previous = receipt["after"]
    gained = exact_names(current) - exact_names(initial)
    lost = exact_names(initial) - exact_names(current)
    recertified, changed_source, missing_source = [], [], []
    for name in sorted(gained):
        before_sha = initial[name].get("source_sha256")
        after_sha = current[name].get("source_sha256")
        (recertified if before_sha and before_sha == after_sha else
         changed_source if before_sha and after_sha else missing_source).append(name)
    if progress.get("completed_batches", 0) > len(batches):
        discrepancies.append("progress completed_batches differs from available receipts")
    return {"progress_status": progress.get("status"), "baseline_commit": baseline["commit"],
            "latest_completed_commit": previous["commit"], "completed_batches": len(batches),
            "completed_items": sum(b["work_items"] or 0 for b in batches),
            "new_attempts": sum(b["new_attempts"] for b in batches),
            "batch_wall_seconds": sum(b["wall_seconds"] for b in batches),
            "exact": {"baseline_object_exact_or_integrated": baseline["summary"]["object_exact_or_integrated"],
                      "latest_object_exact_or_integrated": previous["summary"]["object_exact_or_integrated"],
                      "baseline_membership_including_pending_integration": len(exact_names(initial)),
                      "latest_membership_including_pending_integration": len(exact_names(current)),
                      "gained": sorted(gained), "lost": sorted(lost),
                      "new_vs_baseline_raw_ledgers": sorted(gained - research - campaign),
                      "already_in_research_raw": sorted(gained & research),
                      "already_in_campaign_raw": sorted(gained & campaign),
                      "unchanged_source_recertifications": recertified,
                      "changed_source_gains": changed_source,
                      "missing_source_hash_gains": missing_source},
            "profiles": {name: dict(counts) for name, counts in sorted(profiles.items())},
            "parked_new": parked, "batches": batches, "active": active,
            "discrepancies": discrepancies,
            "measurement_limits": ["Per-profile worker wall time is absent from controller log; batch wall and per-profile compile seconds are reported separately.",
                                   "Canary changed_nodes omits parked blocker; newly parked nodes cannot be classified as operational from these receipts alone."]}


def summarize_profiles(rows):
    result = defaultdict(Counter)
    add_profiles(result, rows)
    return {name: dict(counts) for name, counts in sorted(result.items())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--run", type=Path, default=RUN)
    args = parser.parse_args()
    data = report(args.run)
    if args.json:
        print(json.dumps(data, indent=2))
        return
    exact = data["exact"]
    print(f"{data['completed_batches']} batches, {data['completed_items']} items, "
          f"{data['new_attempts']} attempts; status={data['progress_status']}")
    print(f"Object exact or integrated: {exact['baseline_object_exact_or_integrated']} -> "
          f"{exact['latest_object_exact_or_integrated']}; exact membership including "
          f"pending integration: {exact['baseline_membership_including_pending_integration']} -> "
          f"{exact['latest_membership_including_pending_integration']}; "
          f"gained={len(exact['gained'])}, lost={len(exact['lost'])}, "
          f"new vs raw ledgers={len(exact['new_vs_baseline_raw_ledgers'])}")
    print(f"Gains: unchanged source={len(exact['unchanged_source_recertifications'])}, "
          f"changed source={len(exact['changed_source_gains'])}, "
          f"missing hash={len(exact['missing_source_hash_gains'])}")
    print(f"Batch wall={data['batch_wall_seconds']:.1f}s; per-profile work / "
          "compile misses / hits / compile seconds:")
    for name, p in data["profiles"].items():
        print(f"  {name}: {p['work_items']} / {p['compile_misses']} / "
              f"{p['compile_hits']} / {p['compile_seconds']:.1f}")
    if data["parked_new"]:
        print("Newly parked (blocker unavailable in receipt):", data["parked_new"])
    if data["active"]:
        a = data["active"]
        print(f"Active {a['batch']}: {a['completed_log_rows']} completed log rows, "
              f"profiles={a['profiles']}")
        if a["non_json_log_lines"]:
            print("Active non-JSON log lines:", a["non_json_log_lines"])
    if data["discrepancies"]:
        print("DISCREPANCIES:")
        for line in data["discrepancies"]:
            print("  " + line)


if __name__ == "__main__":
    main()

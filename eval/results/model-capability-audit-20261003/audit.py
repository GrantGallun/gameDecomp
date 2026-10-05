"""Read-only aggregate audit; never emits held-out source, prompts, or answers.

Run in WSL: python3 /mnt/c/Code/gameDecomp/eval/results/model-capability-audit-20261003/audit.py
No compiler or model calls. stdout is the audit receipt.
"""
import collections
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
E = Path.home() / "decomp/experiments/edit-capability-20261002"


def rows(path):
    return [json.loads(line) for line in path.open() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    from eval import coverage, logic_tasks

    out = {"source_files": {str(p.relative_to(ROOT)): digest(p) for p in
                           [ROOT / "eval/coverage.py", ROOT / "eval/logic_tasks.py",
                            ROOT / "eval/repair_prompts.py", ROOT / "solver/search_priors.py",
                            ROOT / "solver/site_edits.py"]}}
    panel = {"ids": ["a", "b"], "classes": {"a": "x", "b": "y"}, "sha256": "synthetic-repro"}
    before = [{"id": "a", "exact": True, "compiles": 10}, {"id": "b", "exact": False, "compiles": 1}]
    after = [{"id": "a", "exact": True, "compiles": 9}, {"id": "b", "exact": False, "compiles": 10000}]
    out["cost_repro"] = {"before_total": 11, "after_total": 10009,
                         "comparison": coverage.compare(panel, before, after)}
    out["missing_repro"] = coverage.compare(panel, before, after[:1])
    out["split_repro"] = {str(s): logic_tasks.split_of({"split": s, "repository": "sample"}, set())
                          for s in ["train", "dev", "test", "heldout", "check", None]}
    if not E.exists():
        print(json.dumps(out, indent=2))
        return

    out["runs"] = {}
    names = ["attempts.jsonl", "system_dev_v2.jsonl", "system_heldout_v2.jsonl"]
    names += [p.name for p in sorted(E.glob("cov_heldout*.jsonl"))]
    for name in names:
        rs = rows(E / name)
        rec = {"sha256": digest(E / name), "rows": len(rs),
               "reported_exact": sum(bool(r.get("exact")) or r.get("status") == "exact" for r in rs),
               "recorded_compiles": sum(r.get("compiles", 0) for r in rs)}
        if name == "attempts.jsonl":
            rec["by_level"] = dict(collections.Counter(r.get("level") for r in rs if r.get("status") == "exact"))
        out["runs"][name] = rec
    p = E / "public/logic-v1/tasks.jsonl"
    ts = rows(p)
    manifest = json.loads(p.with_name("manifest.json").read_text())
    out["logic"] = {"manifest": manifest, "actual_sha256": digest(p), "rows": len(ts),
                    "unique_ids": len({t["id"] for t in ts}), "splits": {}}
    for split in ["train", "exam", "check"]:
        ss = [t for t in ts if t["split"] == split]
        by_prompt = collections.defaultdict(set)
        for t in ss:
            by_prompt[t["prompt"]].add(t["completion"])
        out["logic"]["splits"][split] = {
            "tasks": len(ss), "functions": len({(t["repository"], t["function"]) for t in ss}),
            "groups": len({t["split_group"] for t in ss}), "duplicate_prompts": len(ss) - len(by_prompt),
            "conflicting_prompts": sum(len(v) > 1 for v in by_prompt.values()),
            "truncated_diffs": sum(" more rows)" in t["prompt"] for t in ss),
        }
    grouped = {s: {t["split_group"] for t in ts if t["split"] == s} for s in ["train", "exam", "check"]}
    out["logic"]["group_overlaps"] = {a + "/" + b: len(grouped[a] & grouped[b])
                                       for a, b in [("train", "exam"), ("train", "check"), ("exam", "check")]}
    from eval.repair_prompts import load_task_examples
    out["logic"]["existing_source_trainer_examples"] = len(load_task_examples(ts, split="train"))
    out["public_pairs"] = {}
    for name in ["single", "single_same", "multi"]:
        p = E / "public" / (name + ".jsonl")
        rs = rows(p)
        out["public_pairs"][name] = {"rows": len(rs), "sha256": digest(p), "unique_ids": len({r["id"] for r in rs}),
                                      "opts": dict(collections.Counter(r.get("opt") for r in rs)),
                                      "certificate_fields": sum(any("cert" in k for k in r) for r in rs)}
    out["induction"] = []
    for r in rows(E / "induction.jsonl"):
        ex = set(r["overlap"])
        valid = [(p, t) for i, (p, t) in enumerate(zip(r["predictions"], r["truth"])) if i not in ex]
        out["induction"].append({"topic": r["topic"], "seed": r["seed"], "n": len(valid),
                                  "correct": sum(p == t for p, t in valid),
                                  "majority": max(collections.Counter(t for p, t in valid).values()),
                                  "probes": len(r["probes"]), "overlap": len(ex),
                                  "failed_probes": sum(p.get("verdict") == "does-not-compile" for p in r["probes"])})
    out["failed_probe_examples"] = []
    for r in rows(E / "induction.jsonl"):
        if r["topic"] in ("empty_arm", "commutative_operands") and r["seed"] == 1:
            p = r["probes"][0]
            out["failed_probe_examples"].append({"topic": r["topic"], "a": p["a"], "b": p["b"],
                                                  "opt": p["opt"], "verdict": p["verdict"]})
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

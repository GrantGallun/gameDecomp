"""One machine-readable receipt for the second half of the round.

Aggregates the receipts that already exist -- the safety fix's two search arms, the registry search that
closed a function, the deeper run on the two largest gains, and the codegen reading -- and adds nothing of
its own. The verified solution's identity is copied from the search receipt; `_verify_solution.py` is what
established that it recompiles exact.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def destructive(path: Path) -> int:
    """Moves that turned a COMPILING candidate into an uncompilable one, for receipts that predate the
    field. Computed from the node log rather than assumed to be zero."""
    payload = load(path.name)
    if payload.get("destructive_moves") is not None:
        return payload["destructive_moves"]
    total = 0
    for record in payload["results"]:
        by_hash = {node["sha256"]: node for node in record["nodes"] if node.get("sha256")}
        for node in record["nodes"]:
            if node.get("compiled") is False and node.get("parent_sha256"):
                parent = by_hash.get(node["parent_sha256"])
                if parent and parent.get("compiled"):
                    total += 1
    return total


fixed = load("search-registry-policy2.json")
closed = next(r for r in fixed["results"] if r["search"].get("exact"))
deep = load("search-deep-two.json")
shape = load("codegen-shape.json")

transfer = []
for record in fixed["results"]:
    transfer.append({"function": record["function"],
                     "policy_score": record["policy"]["score"],
                     "best_score": record["search"]["final_score"],
                     "exact": bool(record["search"]["exact"]),
                     "improved": (record["search"]["final_score"] or 0)
                                 > (record["policy"]["score"] or 0) + 1e-9,
                     "assistance_tier": record["assistance"]["tier"],
                     "compiles": record["search"]["compiles_used"]})

payload = {
    "schema_version": 1,
    "round": "2026-09-21 catalog-safety and the first closed function",
    "regime": ("DEVELOPMENT work on the exposed 200-state frame. Nothing was promoted into the build, no "
               "model was called, and the matched function is header-assisted, never SOLVED."),
    "safety_fix": {
        "defect": ("compile_obligations.opaque_variant appended `typedef struct RacePlayer RacePlayer;` "
                   "because its alias guard could not see a typedef with a body; cfe answered "
                   "`redeclaration of 'RacePlayer'`"),
        "destructive_moves_before": {"from_policy": destructive(HERE / "search-policy.json"),
                                     "from_draft": destructive(HERE / "search-d2-b3.json")},
        "destructive_moves_after": {"from_policy": destructive(HERE / "search-policy-fixed.json"),
                                    "from_draft": destructive(HERE / "search-draft-fixed.json")},
        "registry_catalog_destructive": fixed.get("destructive_moves"),
        "registry_catalog_destructive_action": "redraft (by design: it throws the candidate away and "
                                               "re-drafts from the target assembly)",
    },
    "closed_function": {
        "function": closed["function"],
        "action": closed["search"]["solution_path"],
        "policy_score": closed["policy"]["score"],
        "score_after": closed["search"]["final_score"],
        "exact": closed["search"]["exact"],
        "solution_sha256": closed["solution_sha256"],
        "assistance_tier": closed["assistance"]["tier"],
        "compiles": {"internal": closed["search"]["internal_compiles"],
                     "certifying": closed["search"]["compiles_used_certifying"]},
        "verified_by": "eval/results/dev-set-20260921/_verify_solution.py",
        "independence": ("the exact attempts for this function before this candidate were 0, and the "
                         "recorded bytes hash to the recorded digest and recompile exact"),
        "not_done": "promotion into the real build (requires_isolated_integration, the ratchet's test)",
    },
    "transfer": {"states": len(transfer), "closed": sum(1 for row in transfer if row["exact"]),
                 "improved": sum(1 for row in transfer if row["improved"]),
                 "unchanged": sum(1 for row in transfer if not row["improved"]), "per_state": transfer},
    "deeper_search_on_the_two_largest_gains": {
        "receipt": "search-deep-two.json", "allowance_per_state": deep["compile_allowance_per_state"],
        "compiles_used": deep["compiles_used"],
        "outcome": "no further gain: both are local optima of this catalog"},
    "residual_reading": {"receipt": "codegen-shape.json",
                         "summary": {name: {"score": entry["score"], "instr_delta": entry["instr_delta"],
                                            "faults": {k: v for k, v in entry["faults"].items() if v}}
                                     for name, entry in shape["functions"].items()}},
    "sources": [f"eval/results/dev-set-20260921/{name}" for name in (
        "search-d2-b3.json", "search-draft-fixed.json", "search-policy.json", "search-policy-fixed.json",
        "search-registry-policy2.json", "search-deep-two.json", "codegen-shape.json",
        "registry-declines.json", "dev-set.json", "_verify_solution.py", "RESULT.md")],
}
out = HERE / "ROUND2.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in payload.items() if k != "sources"}, indent=2)[:2200])
print("written", out)

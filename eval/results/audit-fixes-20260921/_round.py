"""One machine-readable receipt for this round: the measurement, the dev set and both search arms.

It AGGREGATES the receipts that already exist and adds nothing of its own. Every number below can be
traced to a file named in `sources`, and each is re-read here rather than copied by hand.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
INTAKE = ROOT / "eval/results/intake-20260921"
DEVSET = ROOT / "eval/results/dev-set-20260921"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def levels(payload: dict) -> dict:
    rows = payload["rows"]
    frontend = 0
    both = 0
    for row in rows:
        trace = row.get("diagnostic_trace")
        passed = ((trace[-1].get("clang") or "") == "passed" if trace
                  else bool(row["sequence"].get("frontend_passed")))
        frontend += int(passed)
        both += int(bool(row["sequence"]["compiled"]) and passed)
    return {"rows": len(rows),
            "ido_compiled": sum(1 for r in rows if r["sequence"]["compiled"]),
            "ido_and_frontend_passed": both,
            "byte_exact": sum(1 for r in rows if r["sequence"]["exact"]),
            "frontend_passed": frontend}


acceptance = load(INTAKE / "wide-intake-acceptance2.json")
dev = load(DEVSET / "dev-set.json")
search_draft = load(DEVSET / "search-d2-b3.json")
search_policy = load(DEVSET / "search-policy.json")

payload = {
    "schema_version": 1,
    "round": "2026-09-21 audit-followup",
    "regime": ("DEVELOPMENT work on the exposed 200-state frame. Nothing was promoted, no model was "
               "called, and no number here is a capability claim."),
    "intake": {
        "frame": "eval/results/intake-20260921/wide-frame.json",
        "frame_size": len(acceptance["rows"]),
        "shortfall": acceptance["shortfall"],
        "levels_now": levels(acceptance),
        "levels_before_and_after": {
            name: levels(load(INTAKE / name)) for name in
            ("wide-intake-traced.json", "wide-intake-undeclared.json", "wide-intake-orfix.json",
             "wide-intake-acceptance2.json")},
        "per_tier": acceptance["by_tier"],
        "residual_classes_on_final_candidate": acceptance["residual_classes_on_final_candidate"],
        "harness_clean": acceptance["harness_clean"],
    },
    "development_set": {
        "selected": dev["selection"]["selected"],
        "excluded": dev["selection"]["excluded"],
        "unrecoverable": dev["selection"]["unrecoverable"],
        "fresh_recompiles_matching_the_recorded_score": sum(
            1 for entry in dev["entries"]
            if (entry.get("fresh") or {}).get("score") == entry["recorded"]["score"]),
        "assistance_tiers": {tier: sum(1 for entry in dev["entries"]
                                       if entry["assistance"]["tier"] == tier)
                             for tier in ("binary-only", "header-assisted",
                                          "reference-source-assisted")},
        "training_eligible": sum(1 for entry in dev["entries"] if entry["training_eligible"]),
    },
    "bounded_search": {
        "depth": search_draft["depth"], "beam": search_draft["beam"],
        "compile_allowance_per_state": search_draft["compile_allowance_per_state"],
        "from_draft": {"states": search_draft["states"], "summary": search_draft["summary"],
                       "compiles_used": search_draft["compiles_used"],
                       "improved_on_the_fixed_order": sum(
                           1 for r in search_draft["results"]
                           if r["classification"].get("sub_outcome") ==
                           "improved-over-the-fixed-order")},
        "from_the_policy_candidate": {
            "states": search_policy["states"], "summary": search_policy["summary"],
            "compiles_used": search_policy["compiles_used"],
            "states_with_no_move_at_all": sum(
                1 for r in search_policy["results"]
                if not [n for n in r["nodes"] if n.get("action") != "start"]),
            "actions_that_still_fire": sorted({
                n["action"] for r in search_policy["results"] for n in r["nodes"]
                if n.get("action") not in (None, "start")}),
            "fires_that_destroy_the_candidate": sum(
                1 for r in search_policy["results"] for n in r["nodes"]
                if n.get("action") not in (None, "start") and n.get("compiled") is False),
        },
    },
    "sources": [str(path.relative_to(ROOT)) for path in (
        INTAKE / "wide-frame.json", INTAKE / "wide-intake-traced.json",
        INTAKE / "wide-intake-undeclared.json", INTAKE / "wide-intake-orfix.json",
        INTAKE / "wide-intake-acceptance2.json", DEVSET / "dev-set.json",
        DEVSET / "search-d2-b3.json", DEVSET / "search-policy.json",
        HERE / "audit-probes-after.json", HERE / "reproduce_budget.py",
        HERE / "reproduce_manifest.py", HERE / "reproduce_boundary.py", HERE / "REPORT.md")],
}
out = HERE / "ROUND.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in payload.items() if k != "sources"}, indent=2)[:2600])
print("written", out)

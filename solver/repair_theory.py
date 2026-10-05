"""Conditional repair goals and local tests, distinct from compiler evidence.

An unsuccessful candidate tests one approach under one set of prerequisites.
It cannot establish that every C representation or repair route is impossible.
"""
from collections import Counter
from copy import deepcopy
import re

from eval.search_replay import digest


BLOCKERS = {
    "signature": r"conflicting types|redeclaration|redefinition",
    "members": r"no member named|has no member|member reference .*not (?:a structure|a pointer)",
    "calls": r"too (?:many|few) arguments|implicit declaration of function",
}


def observation(verdict):
    front = verdict.get("frontend") or {}
    text = front.get("diagnostics") or ""
    errors = re.findall(r"(?m)^.*?: (?:fatal )?error: (.*)$",text)
    counts = Counter(next((name for name,pattern in BLOCKERS.items() if re.search(pattern,e)),"other") for e in errors)
    total = re.search(r"(\d+) errors? generated\.\s*$",text)
    complete = (front.get("passed") is True and not errors) or (
        front.get("status") == "rejected" and bool(total) and int(total[1]) == len(errors)
        and "too many errors emitted" not in text and len(text) < 16000)
    return {"compiled":verdict["compiled"],"frontend":front.get("passed"),"exact":verdict["exact"],
            "blockers":dict(counts),"diagnostics_complete":bool(complete),"error":verdict.get("error")}


def assess(source,verdict,routes):
    """Project a validated compiler observation and guarded routes into a map."""
    observed = observation(verdict)
    ids = set()
    for route in routes:
        if (not isinstance(route["id"],str) or route["id"] in ids or not route["owner"]
                or route["status"] not in {"ready","blocked"}
                or (route["status"] == "ready") != bool(route["candidates"])
                or not isinstance(route["requires"],list)
                or not set(route["addresses"]) <= set(BLOCKERS)|{"other"}):
            raise ValueError("invalid theory route")
        ids.add(route["id"])
        for candidate in route["candidates"]:
            if digest(candidate["source"]) != candidate["source_sha256"] or candidate["source"] == source:
                raise ValueError("invalid theory candidate source")
    goals = {
        "exact":{"requires":["compilation","frontend","object"],"status":"observed" if observed["exact"] else "open"},
        "compilation":{"requires":[],"status":"observed" if observed["compiled"] else "open"},
        "frontend":{"requires":[],"status":"observed" if observed["frontend"] is True else "open"},
        "object":{"requires":["compilation"],"status":"observed" if observed["exact"] else "open"},
    }
    for name in BLOCKERS:
        goals[name] = {"requires":[],"status":"open" if observed["blockers"].get(name) else
                       "observed-clear" if observed["diagnostics_complete"] else "unknown"}
    result = {"schema_version":1,"kind":"conditional-repair-theory-map","source_sha256":digest(source),
        "inputs":{"source":source,"verdict":deepcopy(verdict),"routes":deepcopy(routes)},
        "goals":goals,"blockers":observed["blockers"],"diagnostics_complete":observed["diagnostics_complete"],
        "routes":deepcopy(routes),"feasibility":"witnessed-current-candidate" if observed["exact"] else "conditional-not-proven",
        "edges":[{"from":dep,"to":goal,"kind":"requires-all"} for goal,g in goals.items() for dep in g["requires"]]
              + [{"from":r["id"],"to":g,"kind":"alternative-hypothesis"} for r in routes for g in r["addresses"]],
        "training_eligible":False,"scope":"guarded local approaches; not a proof of global solvability"}
    result["sha256"] = digest(result)
    return result


def validate_map(mapping):
    try:
        original = mapping["inputs"]
        reconstructed = assess(original["source"],original["verdict"],original["routes"])
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError("invalid theory map structure") from exc
    if mapping != reconstructed:
        raise ValueError("theory map differs from its inputs")
    return mapping


def effect(before,after,addresses):
    """Separate local predictions from the conjunctive whole-function goal."""
    a,b = observation(before),observation(after)
    cleared = sorted(k for k in addresses if a["blockers"].get(k) and not b["blockers"].get(k)) if b["diagnostics_complete"] else []
    reduced = sorted(k for k in addresses if b["blockers"].get(k,0) < a["blockers"].get(k,0)) if a["diagnostics_complete"] and b["diagnostics_complete"] else []
    tested = set(addresses)&set(a["blockers"])
    status = "infrastructure-error" if b["error"] or a["error"] else "unassessed" if not b["diagnostics_complete"] or not tested else (
        "prediction-supported" if cleared else "partial-support" if reduced else "prediction-not-met")
    if b["error"] or a["error"]:
        cleared,reduced = [],[]
    return {"local_result":status,"cleared":cleared,"reduced":reduced,"remaining":sorted(b["blockers"]),
            "new_blockers":sorted(set(b["blockers"])-set(a["blockers"])) if a["diagnostics_complete"] else [],
            "goal_reached":bool(after["exact"] and not b["error"]),
            "scope":"this candidate and predicted blocker classes only"}

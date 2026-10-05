"""Descriptive, target-normalized conditional repair transitions.

One target contributes one unit per conditional row, however often it was
compiled. A further unit is reserved for unknown outcomes. This smoothing is
declared, not a claim of calibrated success probabilities or statistical IID.
"""
from collections import defaultdict
import math
import re

from eval.repair_graph import validate_graph
from eval.search_replay import digest
from solver.repair_rules import CATALOG, SCHEMA_VERSION, action_key, proposals, state_features


def _key(state, action):
    return digest({"state": state, "action": action})


def fit(graph, *, development_targets):
    validate_graph(graph)
    targets = {n["identity"]["target_sha256"] for n in graph["nodes"].values()}
    if not targets <= set(development_targets):
        raise ValueError("every input target needs an explicit development declaration")
    abstract = {sid: state_features(n["source"], n["function"], n["verdict"])
                for sid,n in graph["nodes"].items()}
    rows, observations = {}, defaultdict(lambda: defaultdict(dict))
    for edge in graph["edges"]:
        if edge["family"] not in CATALOG:
            continue
        parent, child = [graph["nodes"][edge[k]] for k in ("parent", "child")]
        guarded = proposals(parent["source"], parent["function"], parent["verdict"])
        if not any(p["family"] == edge["family"] and p["source"] == child["source"] for p in guarded):
            raise ValueError("observed edge is not the declared guarded repair")
        action = action_key(edge["family"], parent["source"], child["source"])
        state, next_state = abstract[edge["parent"]], abstract[edge["child"]]
        key = _key(state, action)
        rows.setdefault(key, {"state": state, "action": action, "evidence_edges": []})
        rows[key]["evidence_edges"].append(edge["id"])
        target = parent["identity"]["target_sha256"]
        # Different receipts / labels for the same concrete transition are one observation.
        transition = digest([edge["parent"], action, edge["child"]])
        observations[key][target][transition] = next_state
    for key, groups in observations.items():
        weights, states = defaultdict(float), {}
        for transitions in groups.values():
            for outcome in transitions.values():
                oid = digest(outcome)
                states[oid] = outcome
                weights[oid] += 1 / len(transitions)
        support = len(groups)
        rows[key].update(support_targets=support, unknown_mass=1/(support+1),
            outcomes=[{"state": states[oid], "mass": mass/(support+1)} for oid,mass in sorted(weights.items())])
    model = {"schema_version": 1, "feature_schema": SCHEMA_VERSION, "kind": "conditional-repair-transitions",
        "use": "explicit-development-analysis", "training_eligible": False,
        "graph_sha256": graph["sha256"], "development_targets": sorted(targets), "rows": rows,
        "smoothing": "one unknown pseudocount; one unit per independent target per conditional row",
        "limitations": "descriptive adaptive observations, uncalibrated; no transfer claim"}
    model["sha256"] = digest(model)
    return model


def validate_model(model, *, graph=None):
    payload = {k:v for k,v in model.items() if k != "sha256"}
    if model.get("sha256") != digest(payload):
        raise ValueError("model checksum mismatch")
    if (model.get("kind") != "conditional-repair-transitions" or model.get("feature_schema") != SCHEMA_VERSION
            or model.get("schema_version") != 1 or model.get("training_eligible") is not False
            or model.get("use") != "explicit-development-analysis"):
        raise ValueError("model schema mismatch")
    actions = {"address_reuse", "parameter_reuse", *(f"register_storage:{i}" for i in range(1,9))}
    residuals = {"exact", "compile_failed", "spills", "load_base", "certificate_only",
                 "structural", "regalloc", "ordering", "reloc", "layout", "immediate", "other"}
    def sha(value):
        return isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value)
    def state(value):
        if (set(value) != {"schema_version","domain","residual","available"}
                or value["schema_version"] != SCHEMA_VERSION or not sha(value["domain"])
                or value["residual"] not in residuals or not isinstance(value["available"],list)
                or value["available"] != sorted(set(value["available"]))
                or not set(value["available"]) <= actions
                or (value["residual"] in {"exact","compile_failed"} and value["available"])):
            raise ValueError("invalid abstract state")
    try:
        targets = model["development_targets"]
        if targets != sorted(set(targets)) or not all(sha(t) for t in targets) or not sha(model["graph_sha256"]):
            raise ValueError("invalid model evidence identity")
        for key,row in model["rows"].items():
            state(row["state"])
            support = row["support_targets"]
            if (key != _key(row["state"],row["action"]) or row["action"] not in row["state"]["available"]
                    or type(support) is not int or not 1 <= support <= len(targets)
                    or row["unknown_mass"] != 1/(support+1)
                    or not row["evidence_edges"] or not all(sha(e) for e in row["evidence_edges"])):
                raise ValueError("invalid conditional row")
            seen, total = set(), row["unknown_mass"]
            for outcome in row["outcomes"]:
                state(outcome["state"])
                mass, oid = outcome["mass"], digest(outcome["state"])
                if (type(mass) not in (float,int) or not math.isfinite(mass) or not 0 < mass <= 1
                        or oid in seen or outcome["state"]["domain"] != row["state"]["domain"]):
                    raise ValueError("invalid transition outcome")
                seen.add(oid)
                total += mass
            if not math.isclose(total,1,rel_tol=0,abs_tol=1e-12):
                raise ValueError("transition masses must sum to one")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid model structure") from exc
    if graph is not None and model != fit(graph,development_targets=targets):
        raise ValueError("model differs from its development evidence")
    return model


def estimate(model, state, action):
    # Public callers validate the model once at the boundary; this function is
    # also used recursively many times per controller decision.
    return model["rows"].get(_key(state, action), {"state": state, "action": action,
        "support_targets": 0, "unknown_mass": 1.0, "outcomes": [], "evidence_edges": []})


def value(model, state, action, *, horizon=2):
    """Mass assigned to known routes to exactness; unknown routes stay explicit.

    This is a planning heuristic, not a confidence bound. Only the first action
    is grounded in the current source. Later actions must pass real guards when
    their parent has actually been compiled. No score/cost enters this value.
    """
    if type(horizon) is not int or not 1 <= horizon <= 2:
        raise ValueError("horizon must be one or two")
    row = estimate(model, state, action)
    result = {"action": action, "exact_mass": 0.0, "support_targets": row["support_targets"],
              "unknown_mass": row["unknown_mass"], "outcomes": [], "evidence_edges": row["evidence_edges"]}
    for outcome in row["outcomes"]:
        future = outcome["state"]
        exact = float(future["residual"] == "exact")
        continuation = None
        if not exact and horizon > 1 and future["residual"] != "compile_failed":
            choices = [value(model, future, a, horizon=horizon-1) for a in future["available"]]
            if choices:
                best = max(choices, key=lambda c:c["exact_mass"])
                if best["exact_mass"]:
                    continuation = {**best, "hypothetical": True}
                    exact = best["exact_mass"]
        result["exact_mass"] += outcome["mass"] * exact
        result["outcomes"].append({**outcome, "continuation": continuation})
    return result

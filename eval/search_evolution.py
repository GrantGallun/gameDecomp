"""Bounded, observation-derived scheduler proposals and a strict laboratory gate.

This changes search policy, never generators, compiler, verifier or model weights.
Research observations propose numeric depth penalties; fresh paired certificates
decide succession. An efficiency gain alone cannot advance a capability generation.
"""
from dataclasses import asdict
import math

from eval.search_replay import Policy, Replay, digest, merge_worlds, run, validate_world


def _panel(worlds, partition):
    if not worlds:
        raise ValueError("empty panel")
    tasks = []
    for world in worlds:
        validate_world(world)
        context = world["context"]
        if context.get("partition") != partition:
            raise ValueError(f"expected {partition} observations")
        tasks.append(context["task"])
    if len(set(tasks)) != len(tasks):
        raise ValueError("duplicate panel task")
    return tasks


def propose(worlds, parent):
    """At most four recipes, using only observed positive score-step magnitudes.

    The proposer is a fixed deterministic algorithm, not an LLM researcher.
    Neither function names nor candidate C are policy features. Score steps set
    exploration penalties; they are not labelled successful repairs or exacts.
    """
    _panel(worlds, "research")
    increments = []
    for world in worlds:
        nodes = {n["id"]: n for n in world["nodes"]}
        for node in world["nodes"]:
            if node["parent"] is None or not node["verdict"]["compiled"]:
                continue
            gain = node["verdict"]["score"] - nodes[node["parent"]]["verdict"]["score"]
            if gain > 0:
                increments.append(gain)
    increments.sort()
    proposals = [Policy("greedy", "greedy")]
    if increments:
        penalties = []
        for fraction in (0.25, 0.75):
            value = round(max(0.125, min(32.0, increments[int((len(increments) - 1) * fraction)])), 6)
            penalties.append(value)
        # Widely separated quantiles can miss the range that preserves both
        # useful descent and exploration. Sample their midpoint in log space.
        penalties.append(round(math.sqrt(penalties[0] * penalties[-1]), 6))
        for value in penalties:
            policy = Policy(f"observed-depth-{value:g}", "depth", value)
            if policy not in proposals:
                proposals.append(policy)
    else:
        proposals.append(Policy("breadth", "breadth"))
    return [p for p in proposals if (p.mode, p.quantum) != (parent.mode, parent.quantum)]


def select(worlds, parent, *, budget, proposals=None, regressions=()):
    """Research selection, optionally constrained by explicit development cases.

    Regression wrappers preserve their worlds' original evaluation partition.
    Using them is retrospective development, never a new held-out measurement.
    They do not enter the proposer or model training.
    """
    tasks = _panel(worlds, "research")
    if type(budget) is not int or budget <= 0:
        raise ValueError("positive integer budget required")
    policies = [parent] + (propose(worlds, parent) if proposals is None else list(proposals))
    if len(policies) > 5 or len({p.name for p in policies}) != len(policies):
        raise ValueError("bounded unique policy candidates required")
    if any(case.get("kind") != "development-regression" or "world" not in case for case in regressions):
        raise ValueError("explicit development-regression wrappers required")
    regression_worlds = [case["world"] for case in regressions]
    regression_tasks = _panel(regression_worlds, "evaluation") if regression_worlds else []
    if set(tasks) & set(regression_tasks):
        raise ValueError("research and regression tasks overlap")
    rows = []
    for policy in policies:
        results = [run(Replay(w), policy, budget) for w in worlds]
        regression_results = [run(Replay(w), policy, budget) for w in regression_worlds]
        errors = any(n["verdict"].get("error") for w in list(worlds) + regression_worlds for n in w["nodes"])
        eligible = all(r["complete"] for r in results + regression_results) and not errors
        rows.append({"policy": asdict(policy), "results": results, "eligible": eligible,
                     "regression_results": regression_results,
                     "regression_lost": [t for t, r in zip(regression_tasks, regression_results) if not r["exact"]]})
    baseline = rows[0]
    baseline["eligible"] = baseline["eligible"] and not baseline["regression_lost"]
    selected = baseline
    def key(row):
        return (sum(r["exact"] for r in row["results"]),
                round(sum(r["best_score"] - r["baseline_score"] for r in row["results"]), 9),
                -sum(r["compiles"] for r in row["results"]))
    for row in rows[1:]:
        row["lost"] = [task for task, before, after in zip(tasks, baseline["results"], row["results"])
                       if before["exact"] and not after["exact"]]
        if (baseline["eligible"] and row["eligible"] and not row["lost"]
                and not row["regression_lost"] and key(row) > key(selected)):
            selected = row
    return {"candidate": selected["policy"], "parent": asdict(parent), "eligible": baseline["eligible"],
            "candidate_results": selected["results"], "policies": rows, "budget": budget,
            "research_tasks": tasks, "world_sha256": [digest(w) for w in worlds],
            "regression_tasks": regression_tasks, "regression_world_sha256": [digest(w) for w in regression_worlds],
            "regression_use": "explicit retrospective development; not held-out transfer or model training",
            "objective": "no lost exacts; exact count, score progress, then fewer compiles; parent wins ties"}


def gate(parent_worlds, candidate_worlds, parent, candidate, *, budget, research_tasks,
         retention_parent=(), retention_candidate=()):
    """Recompute both arms at one ceiling; metadata-only success cannot advance."""
    if type(budget) is not int or budget <= 0:
        raise ValueError("positive integer budget required")
    tasks = _panel(parent_worlds, "evaluation")
    if set(tasks) != set(_panel(candidate_worlds, "evaluation")) or set(tasks) & set(research_tasks):
        raise ValueError("panel mismatch or research overlap")
    candidates = {w["context"]["task"]: w for w in candidate_worlds}
    before, after = [], []
    for world in parent_worlds:
        other = candidates[world["context"]["task"]]
        if world["context"] != other["context"] or world["max_depth"] != other["max_depth"]:
            raise ValueError("paired context mismatch")
        # Identical environments must agree on every shared expansion and on
        # stream exhaustion. Keep each arm's actual coverage for its own replay.
        merge_worlds([world, other])
        for arm in (world, other):
            if any(n["verdict"].get("error") for n in arm["nodes"]):
                raise ValueError("infrastructure error in evaluation")
        before.append(run(Replay(world), parent, budget))
        after.append(run(Replay(other), candidate, budget))
    if not all(r["complete"] for r in before + after):
        raise ValueError("incomplete evaluation")
    gained = [t for t, b, a in zip(tasks, before, after) if a["exact"] and not b["exact"]]
    lost = [t for t, b, a in zip(tasks, before, after) if b["exact"] and not a["exact"]]
    retention = None
    if retention_parent or retention_candidate:
        if set(_panel(retention_parent, "evaluation")) & set(tasks):
            raise ValueError("retention and new evaluation panels overlap")
        retention = gate(retention_parent, retention_candidate, parent, candidate,
                         budget=budget, research_tasks=research_tasks)
    retention_lost = retention["lost"] if retention else []
    advance = bool(gained) and not lost and not retention_lost
    return {"advance": advance, "gained": gained, "lost": lost,
            "retention_lost": retention_lost, "retention": retention,
            "reason": "certified gain without loss" if advance else "lost certified matches" if lost or retention_lost else "no certified gain",
            "parent": asdict(parent), "candidate": asdict(candidate), "budget": budget,
            "parent_compiles": sum(r["compiles"] for r in before),
            "candidate_compiles": sum(r["compiles"] for r in after),
            "parent_results": before, "candidate_results": after,
            "world_sha256": {"parent": [digest(w) for w in parent_worlds],
                             "candidate": [digest(w) for w in candidate_worlds]}}


def compile_logged(ws, repo, function, source, *, conn, **metadata):
    """A compiler refusal is an observation; an infrastructure exception is not."""
    from solver import workspace
    from eval.tool_agent_run import _attempt_to_verdict
    error = None
    try:
        attempt = workspace.score(ws, repo, function, source, conn=conn, func=function, **metadata)
    except Exception as exc:
        error = "compiler-infrastructure-exception"
        attempt = workspace.Attempt(False, 0.0, False, "", f"{type(exc).__name__}: {exc}", "")
        workspace.record_attempt(conn, function, source, attempt, **metadata)
    if attempt.receipt_id is None:
        raise RuntimeError("compiler attempt missing durable receipt")
    verdict = _attempt_to_verdict(attempt)
    verdict["exact"] = bool(attempt.exact and (attempt.frontend or {}).get("passed"))
    if error:
        verdict["error"] = error
    return verdict

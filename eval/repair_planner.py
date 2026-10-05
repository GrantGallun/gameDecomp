"""Opt-in, bounded action planning over existing guarded repair proposals.

Predicted continuations live in decision records, never in the observed world.
Only actual compiler callbacks add evidence. No execution cost ranks actions.
"""
from copy import deepcopy

from eval.repair_transitions import validate_model, value
from eval.search_replay import Observation, Policy, digest, validate_world
from eval.search_scheduler import Online
from solver.repair_rules import proposals, state_features


class PreviewLimit(Exception):
    pass


class ActionOnline(Online):
    """The same compiler/checkpoint boundary with selectable, uncompiled actions."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.world["history_kind"] = "action-history"
        self.pending, self.offers, self.proposal_positions = {}, {}, {}
        self.generation_exhausted = set()

    def _close_if_observed(self, parent):
        if parent in self.generation_exhausted and not self.pending[parent] and parent not in self.world["closed"]:
            self.world["closed"].append(parent)
            self._checkpoint()

    def can_expand(self, node):
        return (node["verdict"]["compiled"] and not node["verdict"]["exact"]
                and not node["verdict"].get("error") and node["id"].count("/") < self.max_depth)

    def variants_for(self, node):
        return self.variants(node["source"],node["verdict"].get("diff",""))

    def propose(self, parent, limit):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("preview limit must be between 1 and 32")
        node = self.nodes[parent]
        if not self.can_expand(node):
            raise ValueError("ineligible proposal parent")
        if parent not in self.pending:
            self.pending[parent] = []
            self.streams[parent] = iter(self.variants_for(node))
            self.seen[parent] = set()
            cursor = node
            while cursor is not None:
                self.seen[parent].add(cursor["source_sha256"])
                cursor = self.nodes[cursor["parent"]] if cursor["parent"] is not None else None
        scans = 0
        while len(self.pending[parent]) < limit and parent not in self.generation_exhausted:
            if scans == 128:
                if not self.pending[parent]:
                    raise PreviewLimit(parent)
                break
            scans += 1
            try:
                label, family, source = next(self.streams[parent])
            except StopIteration:
                self.generation_exhausted.add(parent)
                break
            sha = digest(source)
            if sha in self.seen[parent]:
                continue
            self.seen[parent].add(sha)
            index = self.proposal_positions.get(parent, 0)
            self.proposal_positions[parent] = index + 1
            offer = {"parent": parent, "index": index, "label": label, "family": family,
                     "source": source, "source_sha256": sha}
            offer["id"] = digest(offer)
            self.pending[parent].append(offer)
            self.offers[offer["id"]] = offer
        self._close_if_observed(parent)
        return deepcopy(self.pending[parent])

    def commit(self, proposal_id):
        if proposal_id not in self.offers:
            raise ValueError("unknown or already consumed proposal")
        offer = self.offers.pop(proposal_id)
        parent = offer["parent"]
        self.pending[parent].remove(offer)
        ordinal = self.positions.get(parent, 0)
        self.positions[parent] = ordinal + 1
        node = self._compile(offer["source"], offer["label"], offer["family"], parent, ordinal)
        node.update(proposal_id=proposal_id, proposal_index=offer["index"])
        self._checkpoint()
        self._close_if_observed(parent)
        return node


def run_planner(environment, model, *, budget, horizon=2, preview=8, fallback=None):
    validate_model(model)
    if type(budget) is not int or budget < 0:
        raise ValueError("nonnegative integer budget required")
    if type(horizon) is not int or not 1 <= horizon <= 2:
        raise ValueError("horizon must be one or two")
    if type(preview) is not int or not 1 <= preview <= 32:
        raise ValueError("preview must be between 1 and 32")
    fallback = fallback or Policy("depth-1.18754", "depth", 1.18754)
    result = {"compiles": 0, "exact": False, "best_score": 0.0, "baseline_score": 0.0,
              "best_id": None, "trace": [], "stop": "budget", "complete": True,
              "recorded_seconds": 0.0, "decisions": [], "model_sha256": model["sha256"]}
    if budget == 0:
        return result
    features, guarded = {}, {}

    def reveal(node):
        identity, verdict = node["id"], node["verdict"]
        result["trace"].append(identity)
        result["compiles"] += 1
        result["recorded_seconds"] += verdict.get("seconds", 0)
        if result["best_id"] is None or verdict["exact"] or (verdict["compiled"] and verdict["score"] > result["best_score"]):
            result["best_id"], result["best_score"] = identity, verdict["score"]
        result["exact"] = result["exact"] or verdict["exact"]
        if verdict.get("error"):
            result.update(complete=False, stop="infrastructure-error")
        return result["complete"]

    root = environment.start()
    reveal(root)
    result["baseline_score"] = root["verdict"]["score"]
    while result["compiles"] < budget and not result["exact"] and result["complete"]:
        offers, visible = [], []
        try:
            for identity,node in list(environment.nodes.items()):
                verdict = node["verdict"]
                if not environment.can_expand(node):
                    continue
                pending = environment.propose(identity, preview)
                if pending:
                    offers.extend(pending)
                    visible.append(Observation(identity,identity.count("/"),verdict["score"],environment.positions.get(identity,0)))
        except PreviewLimit:
            result.update(complete=False, stop="preview-limit")
            break
        if not offers:
            result["stop"] = "exhausted"
            break
        ranking, remaining = {}, list(visible)
        while remaining:
            selected = fallback.choose(tuple(remaining))
            ranking[selected] = len(ranking)
            remaining = [o for o in remaining if o.id != selected]
        choices = []
        for offer in offers:
            parent = environment.nodes[offer["parent"]]
            pid = parent["id"]
            if pid not in features:
                features[pid] = state_features(parent["source"],environment.world["context"]["task"],parent["verdict"])
                guarded[pid] = {(p["family"],p["source"]):p["action"] for p in
                    proposals(parent["source"],environment.world["context"]["task"],parent["verdict"])}
            action = guarded[pid].get((offer["family"],offer["source"]))
            remaining_depth = environment.max_depth - pid.count("/")
            prediction = value(model,features[pid],action,horizon=min(horizon,budget-result["compiles"],remaining_depth)) if action else {
                "action": None, "exact_mass": 0.0, "support_targets": 0, "unknown_mass": 1.0, "outcomes": []}
            theory = environment.theory_priority(offer) if hasattr(environment,"theory_priority") else {"rank":[0,0,0]}
            choices.append((offer,prediction,theory))
        offer,prediction,theory = min(choices,key=lambda row:(-row[1]["exact_mass"],
            tuple(-v for v in row[2]["rank"]),ranking[row[0]["parent"]],row[0]["index"]))
        result["decisions"].append({"parent": offer["parent"], "proposal_id": offer["id"],
            "proposal_index": offer["index"], "source_sha256": offer["source_sha256"], "label": offer["label"],
            "family": offer["family"], "state": features[offer["parent"]], "prediction": prediction,
            "reason": "known-continuation" if prediction["exact_mass"] > 0 else "theory-alternative" if theory["rank"][0] else "fallback",
            "budget_remaining": budget-result["compiles"],
            "alternatives": [{"proposal_id": p["id"], "parent":p["parent"], "index":p["index"],
                              "action":v["action"], "exact_mass":v["exact_mass"]} for p,v,t in choices]})
        if hasattr(environment,"theory_priority"):
            result["decisions"][-1]["theory"] = theory
        reveal(environment.commit(offer["id"]))
    if result["exact"]:
        result["stop"] = "exact"
    if hasattr(environment,"theory_summary"):
        result["theory"] = environment.theory_summary(result)
    return result


def replay_planner(world, model, variants, *, budget, horizon=2, preview=8, fallback=None, environment_factory=ActionOnline):
    """Audit a recorded action sequence, not a counterfactual search policy.

    Regenerate proposals with frozen code. Serve a receipt only after the planner
    independently selects the exact next recorded source, label and parent.
    Missing or changed actions invalidate the audit; they are never failures.
    """
    validate_world(world)
    if world.get("history_kind") != "action-history" or not world["nodes"]:
        raise ValueError("planner replay requires a nonempty action history")
    position = 0
    def observed(source,label,parent):
        nonlocal position
        if position >= len(world["nodes"]):
            raise ValueError("planner replay requests unobserved action")
        node = world["nodes"][position]
        if (source,label,parent) != (node["source"],node["label"],node["parent_receipt_id"]):
            raise ValueError("planner replay selected a different action")
        position += 1
        verdict = deepcopy(node["verdict"])
        if "raw_diff" in verdict:
            verdict["diff"] = verdict["raw_diff"]
        return verdict
    environment = environment_factory(world["nodes"][0]["source"],observed,variants,world["context"],max_depth=world["max_depth"])
    result = run_planner(environment,model,budget=budget,horizon=horizon,preview=preview,fallback=fallback)
    if environment.world != world or position != len(world["nodes"]):
        raise ValueError("planner replay does not reproduce the recorded history")
    return result

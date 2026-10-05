"""A theory can justify a new guarded experiment, never invent its outcome."""
from copy import deepcopy

from eval.repair_graph import build_graph
from eval.repair_planner import ActionOnline, PreviewLimit
from eval.search_replay import digest
from solver.repair_theory import assess, effect, observation


CONTEXT = ("target_sha256","compiler_sha256","generator_sha256","assistance")


def assessed_negative(verdict):
    front = verdict.get("frontend") or {}
    if verdict.get("error") or verdict["exact"] or front.get("passed") not in (True,False):
        return False
    if front.get("status") == "unavailable":
        return False
    o = observation(verdict)
    # A complete rejection or an explicit object mismatch can exclude this
    # candidate. Missing checker/certificate evidence cannot exclude a route.
    rejected = front.get("passed") is False and o["diagnostics_complete"]
    certificate = verdict.get("verification") or {}
    mismatch = (certificate.get("exact") is False and not certificate.get("error")
                and certificate.get("schema_version") == 1
                and certificate.get("kind") == "mips_object_section_certificate"
                and certificate.get("status") == "object_sections_differ")
    return bool(front.get("source_sha256") and (rejected or mismatch))


class TheoryOnline(ActionOnline):
    def __init__(self,*args,inspect_routes,prior_worlds=(),guide=True,capability_assessor=None,
                 capability_potential=False,**kwargs):
        if type(capability_potential) is not bool:
            raise ValueError('capability_potential must be a boolean')
        if capability_potential and capability_assessor is None:
            raise ValueError('generated potential requires a source-bound capability assessor')
        super().__init__(*args,**kwargs)
        self.world["allow_failed_parents"] = True
        self.inspect_routes = inspect_routes
        self.guide = guide
        self.capability_assessor = capability_assessor
        self.generate_capability_potential = capability_potential
        self.capability_potential = {}
        self.capabilities,self.capability_transitions = {},[]
        self.capability_conflicts = []
        self.maps,self.effects,self.suppressed = {},[],[]
        self.negative_sources = {}
        self.current_sources = {}
        self.prior_refs = []
        for world in prior_worlds:
            build_graph([world])  # Validate receipts, all source bindings and lineage.
            if any(world["context"].get(k) != self.world["context"].get(k) for k in CONTEXT):
                continue
            ref = digest(world)
            self.prior_refs.append(ref)
            for node in world["nodes"]:
                if assessed_negative(node["verdict"]):
                    self.negative_sources[node["source_sha256"]] = {"world":ref,"receipt_id":node["verdict"]["receipt_id"]}

    def can_expand(self,node):
        return (not node["verdict"]["exact"] and not node["verdict"].get("error")
                and node["id"].count("/") < self.max_depth)

    def _compile(self,*args,**kwargs):
        node = super()._compile(*args,**kwargs)
        verdict = node["verdict"]
        if verdict.get("error"):
            return node
        build_graph([self.world])
        if self.capability_assessor is not None:
            from solver.capability_map import validate_assessment, compare
            assessment = validate_assessment(self.capability_assessor(node['source'],deepcopy(verdict)))
            if (assessment['source_sha256'] != node['source_sha256']
                    or assessment['inputs']['context'] != self.world['context']
                    or assessment['inputs']['verdict'] != verdict):
                raise ValueError('capability assessment binding mismatch')
            self.capabilities[node['id']] = assessment
            if self.generate_capability_potential:
                from solver.capability_operations import from_assessment
                self.capability_potential[node['id']] = from_assessment(assessment)
            if node['parent'] in self.capabilities:
                known = {r['id'] for r in assessment['capabilities']}
                family = node['family']
                if family in known:
                    self.capability_transitions.append(compare(self.capabilities[node['parent']],assessment,contract_id=family))
        self.current_sources[node["source_sha256"]] = {"world":"current","receipt_id":verdict["receipt_id"]}
        if node["parent"] in self.maps:
            parent = self.nodes[node["parent"]]
            route = next((r for r in self.maps[node["parent"]]["routes"] if r["id"] == node["family"]),None)
            if route is not None:
                self.effects.append({"parent":node["parent"],"child":node["id"],"route":route["id"],
                    "parent_receipt_id":node["parent_receipt_id"],"receipt_id":verdict["receipt_id"],
                    "parent_source_sha256":parent["source_sha256"],"source_sha256":node["source_sha256"],
                    "addresses":route["addresses"],**effect(parent["verdict"],verdict,route["addresses"])})
        return node

    def variants_for(self,node):
        rows = self.inspect_routes(node["source"],deepcopy(node["verdict"]))
        for row in rows:
            evidence = row.get("evidence")
            if evidence and (evidence.get("source_sha256") != node["source_sha256"]
                    or evidence.get("target_sha256") != self.world["context"]["target_sha256"]
                    or evidence.get("diagnostics_sha256") != digest((node["verdict"].get("frontend") or {}).get("diagnostics") or "")):
                raise ValueError("theory route evidence binding mismatch")
        mapping = assess(node["source"],node["verdict"],rows)
        self.maps[node["id"]] = mapping
        if node['id'] in self.capabilities:
            from solver.capability_map import inspect_expectations
            capability = self.capabilities[node['id']]
            self.capability_conflicts.extend({'parent':node['id'],**conflict}
                for conflict in inspect_expectations(capability,rows))
        for route in rows:
            for candidate in route["candidates"]:
                if candidate["family"] != route["id"]:
                    raise ValueError("theory candidate must bind its generating route")
                yield candidate["label"],candidate["family"],candidate["source"]
        if node["verdict"]["compiled"]:
            yield from super().variants_for(node)

    def propose(self,parent,limit):
        # A preview may contain only historical duplicates. Refill within a hard
        # bound so unseen alternatives are not silently called exhausted.
        for _ in range(16):
            pending = super().propose(parent,limit)
            for offer in pending:
                old = self.current_sources.get(offer["source_sha256"]) or self.negative_sources.get(offer["source_sha256"])
                if old is not None:
                    self.pending[parent].remove(self.offers.pop(offer["id"]))
                    self.suppressed.append({"parent":parent,"proposal_id":offer["id"],
                        "source_sha256":offer["source_sha256"],"reason":"same source already observed in this run without changed inputs" if old["world"] == "current"
                        else "prior assessed rejection under identical context",**old})
            self._close_if_observed(parent)
            if self.pending[parent] or parent in self.generation_exhausted:
                return deepcopy(self.pending[parent])
        raise PreviewLimit(parent)

    def theory_priority(self,offer):
        if not self.guide:
            return {"rank":[0,0,0],"why":"theory priority disabled for ablation"}
        mapping = self.maps[offer["parent"]]
        route = next((r for r in mapping["routes"] if r["id"] == offer["family"]),None)
        if route is None:
            return {"rank":[0,0,0],"why":"unmodeled source mutation"}
        parent = self.nodes[offer["parent"]]
        seen = [e for e in self.effects if e["parent_source_sha256"] == parent["source_sha256"]]
        novel = not any(e["route"] == route["id"] for e in seen)
        negative = any(e["local_result"] == "prediction-not-met" for e in seen)
        root = observation(self.nodes["root"]["verdict"])
        progress = len(set(root["blockers"])-set(mapping["blockers"])) if mapping["diagnostics_complete"] else 0
        return {"rank":[1,progress,int(novel)],"route":route["id"],"map_sha256":mapping["sha256"],
            "expected_effect":{"reduce_or_clear":route["addresses"]},
            "why":"different guarded approach after negative result" if negative and novel else
                  "newly satisfied prerequisites" if progress else "untested guarded approach under explicit prerequisites",
            "feasibility":"conditional-not-proven"}

    def theory_summary(self,result):
        status = "observed-exact" if result["exact"] else "open-current-routes-exhausted" if result["stop"] == "exhausted" else "open-"+result["stop"]
        def intake_rank(node):
            o = observation(node["verdict"])
            return (o["exact"],o["compiled"] and o["frontend"] is True,o["compiled"],o["frontend"] is True,
                    o["diagnostics_complete"],-sum(o["blockers"].values()),node["verdict"]["score"])
        best = max(self.nodes.values(),key=intake_rank)["id"] if self.nodes else None
        states = []
        for parent,mapping in self.maps.items():
            for route in mapping["routes"]:
                trials = [e for e in self.effects if e["parent"] == parent and e["route"] == route["id"]]
                state = "tested-local-support" if any(e["cleared"] or e["reduced"] for e in trials) else (
                    "tested-prediction-not-met" if any(e["local_result"] == "prediction-not-met" for e in trials)
                    else "tested-unassessed" if trials else route["status"])
                states.append({"parent":parent,"route":route["id"],"status":state,
                    "remaining_candidates":sum(c["source_sha256"] not in self.negative_sources and c["source_sha256"] not in self.current_sources for c in route["candidates"]),
                    "receipt_ids":[e["receipt_id"] for e in trials]})
        summary = {"schema_version":1,"goal_status":status,"maps":deepcopy(self.maps),"effects":deepcopy(self.effects),
            "suppressed":deepcopy(self.suppressed),"prior_worlds":self.prior_refs,
            "best_intake_id":best,"route_outcomes":states,"guide_enabled":self.guide,
            "training_eligible":False,"global_impossibility_established":False}
        if self.capability_assessor is not None:
            summary['capability_envelope'] = {'assessments':deepcopy(self.capabilities),
                'transitions':deepcopy(self.capability_transitions),
                'expectation_conflicts':deepcopy(self.capability_conflicts),
                'authority':'intended machinery contracts and observed receipts; not a solvability proof'}
            if self.generate_capability_potential:
                summary['capability_envelope']['potential'] = deepcopy(self.capability_potential)
        return summary

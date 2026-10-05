from copy import deepcopy

import pytest

from solver.repair_theory import assess, effect, validate_map
from eval.search_replay import digest


def verdict(*messages, compiled=False, exact=False):
    text = ''.join(f"candidate.c:2:3: error: {m}\n" for m in messages)
    text += f"{len(messages)} errors generated.\n" if messages else ""
    return {"compiled":compiled,"exact":exact,"score":0,"receipt_id":1,
            "frontend":{"passed":not messages,"status":"rejected" if messages else "passed","diagnostics":text}}


SIGNATURE = "conflicting types for 'f'"
MEMBERS = "no member named 'unk8' in 'struct State'"


def route(id="header_signature",source="child"):
    return {"id":id,"owner":"tested-owner","addresses":["signature"],
            "requires":["included public declaration","matching ABI"],
            "status":"ready","reason":"guard emitted candidate","candidates":[
                {"label":id,"family":id,"source":source,"source_sha256":digest(source)}]}


def test_goals_require_all_obligations_and_possibility_is_conditional():
    m = assess("parent",verdict(SIGNATURE,MEMBERS),[route()])
    assert m["goals"]["exact"]["requires"] == ["compilation","frontend","object"]
    assert m["goals"]["exact"]["status"] == "open"
    assert m["feasibility"] == "conditional-not-proven"
    assert m["blockers"] == {"signature":1,"members":1}
    assert m["routes"][0]["status"] == "ready"
    assert validate_map(m) == m


def test_negative_result_can_support_one_prerequisite_without_confirming_whole_goal():
    result = effect(verdict(SIGNATURE,MEMBERS),verdict(MEMBERS),["signature"])
    assert result["goal_reached"] is False
    assert result["local_result"] == "prediction-supported"
    assert result["cleared"] == ["signature"]
    assert result["remaining"] == ["members"]
    assert result["scope"] == "this candidate and predicted blocker classes only"


def test_failed_route_does_not_refute_other_routes_or_mark_goal_impossible():
    result = effect(verdict(SIGNATURE),verdict(SIGNATURE),["signature"])
    assert result["local_result"] == "prediction-not-met"
    m = assess("parent",verdict(SIGNATURE),[route(),route("byteview_redraft","different")])
    assert m["goals"]["exact"]["status"] == "open"
    assert len(m["routes"]) == 2 and m["feasibility"] != "impossible"


def test_incomplete_diagnostics_cannot_prove_a_blocker_cleared():
    after = verdict(MEMBERS)
    after["frontend"]["diagnostics"] = after["frontend"]["diagnostics"].replace("1 errors generated.","20 errors generated.")
    result = effect(verdict(SIGNATURE),after,["signature"])
    assert result["local_result"] == "unassessed" and not result["cleared"]


def test_infrastructure_failures_are_not_counterevidence():
    after = {**verdict(),"error":"broken compiler"}
    assert effect(verdict(SIGNATURE),after,["signature"])["local_result"] == "infrastructure-error"


def test_missing_before_evidence_is_not_a_failed_prediction():
    result = effect({"compiled":False,"exact":False},verdict(compiled=True),["signature"])
    assert result["local_result"] == "unassessed"


def test_map_reload_rejects_rehashed_contradictory_goal_status():
    m = assess("parent",verdict(SIGNATURE),[route()])
    m["goals"]["exact"]["status"] = "observed"
    m["sha256"] = digest({k:v for k,v in m.items() if k != "sha256"})
    with pytest.raises(ValueError):
        validate_map(m)

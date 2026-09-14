"""Campaign wiring of the register search: gating, priority, and the profile budget."""
from eval import completion_campaign as campaign
from solver import repair_queue as queue


def byte_node(faults, jobs=0):
    return {"status": "pending", "source_sha256": "s", "source": "x.c", "attempt_id": 1,
            "residual": {"compiled": True, "frontend": {"passed": True}, "faults": faults},
            "semantic_validation": {"status": "observed_pass", "source_sha256": "s"},
            "jobs": [{"profile": "old", "source_sha256": "s", "evidence_key": "other"}] * jobs,
            "instruction_count": 20}


def test_regalloc_search_is_first_for_register_dominant_nodes_only():
    dominant = queue.next_profile(byte_node({"register_allocation": 4, "structural": 1}), 3, campaign.PROFILES)
    assert dominant["name"] == "regalloc_search" and dominant["regalloc_budget"] == 300 and not dominant["model"]
    structural = queue.next_profile(byte_node({"register_allocation": 1, "structural": 5}), 3, campaign.PROFILES)
    assert structural["name"] != "regalloc_search"
    assert campaign.next_profile(byte_node({"structural": 5}), 3)["name"] != "regalloc_search"


def test_high_yield_register_nodes_run_ahead_of_their_visit_band():
    state = {"config": {"model_calls": 3}, "nodes": {
        "fresh_structural": byte_node({"register_allocation": 1, "structural": 5}, jobs=0),
        "visited_register": byte_node({"register_allocation": 4, "offset": 1}, jobs=12),
        "visited_heavy": byte_node({"register_allocation": 9, "structural": 4}, jobs=12)}}
    snapshot, selected = queue.project(state, campaign.PROFILES)
    assert selected[0] == "visited_register"
    items = snapshot["work_items"]
    assert items["visited_register"]["priority"][0] == -1
    assert items["visited_heavy"]["priority"][0] == 6      # dominant but >2 other faults: normal band


def test_profile_budget_reaches_agentrepair():
    import inspect
    from eval import agentrepair
    assert "regalloc_budget" in inspect.signature(agentrepair.run).parameters
    source = inspect.getsource(campaign.execute)
    assert 'regalloc_budget=profile.get("regalloc_budget", 0)' in source

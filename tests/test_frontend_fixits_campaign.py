"""Campaign wiring of the frontend fix-its: lane placement, repetition, priority and the profile flag."""
from eval import completion_campaign as campaign
from solver import repair_queue as queue


def node(compiled=True, frontend=False, jobs=()):
    return {"status": "pending", "source_sha256": "s", "source": "x.c", "attempt_id": 1,
            "residual": {"compiled": compiled, "frontend": {"passed": frontend, "status": "rejected",
                                                            "diagnostics": "candidate.c:2:3: error: x"},
                         "faults": {"register_allocation": 2}},
            "jobs": list(jobs), "instruction_count": 20}


def test_compiled_frontend_rejection_runs_fixits_first():
    profile = queue.next_profile(node(), 3, campaign.PROFILES)
    assert profile["name"] == "frontend_fixits" and profile["frontend_fixits"] and not profile["model"]
    assert profile["lane"] == queue.Lane.FRONTEND.value


def test_uncompiled_nodes_keep_compile_recovery_first():
    assert queue.next_profile(node(compiled=False), 3, campaign.PROFILES)["name"] == "compile_recovery"


def test_fixits_run_once_per_evidence_key():
    first = node()
    key = queue.evidence_key(first)
    visited = node(jobs=[{"profile": "frontend_fixits", "source_sha256": "s", "evidence_key": key}])
    assert queue.next_profile(visited, 3, campaign.PROFILES)["name"] == "compile_recovery"
    passed = node(frontend=True)
    assert queue.next_profile(passed, 3, campaign.PROFILES)["name"] != "frontend_fixits"


def test_fixits_run_ahead_of_their_visit_band():
    old = [{"profile": "old", "source_sha256": "s", "evidence_key": "other"}] * 12
    state = {"config": {"model_calls": 3}, "nodes": {"fresh_uncompiled": node(compiled=False),
                                                     "visited_frontend": node(jobs=old)}}
    snapshot, selected = queue.project(state, campaign.PROFILES)
    assert selected[0] == "visited_frontend" and selected[1]["name"] == "frontend_fixits"
    assert snapshot["work_items"]["visited_frontend"]["priority"][0] == -1


def test_profile_flag_reaches_agentrepair():
    import inspect
    from eval import agentrepair
    assert "frontend_fixits" in inspect.signature(agentrepair.run).parameters
    assert 'frontend_fixits=profile.get("frontend_fixits", False)' in inspect.getsource(campaign.execute)

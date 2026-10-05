"""Campaign wiring of the failure-census profiles: recertify@digest and address_symbols."""
from eval import completion_campaign as campaign
from solver import repair_queue as queue


def node(tmp_path, *, score=90.0, faults=None, source="void f(void) { g((void *)0x593D10); }\n", jobs=(), sha="s1"):
    path = tmp_path / f"{sha}.c"
    path.write_text(source)
    return {"status": "pending", "source_sha256": sha, "source": str(path), "attempt_id": 1, "score": score,
            "residual": {"compiled": True, "frontend": {"passed": True},
                         "faults": faults if faults is not None else {"relocation": 4, "structural": 4}},
            "semantic_validation": {"status": "observed_pass", "source_sha256": sha},
            "jobs": list(jobs), "instruction_count": 20}


def test_address_literals_with_relocation_faults_run_address_symbols_first(tmp_path):
    profile = queue.next_profile(node(tmp_path), 3, campaign.PROFILES)
    assert profile["name"] == "address_symbols" and profile["address_rounds"] == 3 and not profile["model"]
    # No relocation fault, or no literal in the source: ordinary lane work.
    assert queue.next_profile(node(tmp_path, faults={"structural": 4}, sha="s2"), 3, campaign.PROFILES)["name"] != "address_symbols"
    plain = node(tmp_path, source="void f(void) { g(1); h((void *)0); k((u8 *)0x10); }\n", sha="s3")
    assert queue.next_profile(plain, 3, campaign.PROFILES)["name"] != "address_symbols"
    # A dereferenced hardware register is not an address-symbol candidate.
    hardware = node(tmp_path, source="void f(void) { (*(volatile u32 *)0xA4800000u) = 1; }\n", sha="s4")
    assert queue.next_profile(hardware, 3, campaign.PROFILES)["name"] != "address_symbols"


def test_address_symbols_runs_once_per_source(tmp_path):
    ran = node(tmp_path, jobs=[{"profile": "address_symbols", "source_sha256": "s1", "evidence_key": "k"}])
    assert queue.next_profile(ran, 3, campaign.PROFILES)["name"] != "address_symbols"


def test_score_100_nodes_recertify_once_per_certificate_code_and_source(tmp_path):
    full = node(tmp_path, score=100.0, faults={})
    profile = queue.next_profile(full, 3, campaign.PROFILES)
    assert profile["name"] == "recertify@" + queue.certificate_digest() and not profile["model"]
    assert len(queue.certificate_digest()) == 16
    done = node(tmp_path, score=100.0, faults={}, jobs=[{"profile": profile["name"], "source_sha256": "s1"}])
    assert not queue.next_profile(done, 3, campaign.PROFILES)["name"].startswith("recertify@")
    stale = node(tmp_path, score=100.0, faults={}, jobs=[{"profile": "recertify@0000000000000000", "source_sha256": "s1"}])
    assert queue.next_profile(stale, 3, campaign.PROFILES)["name"] == profile["name"]


def test_census_profiles_run_ahead_of_their_visit_band(tmp_path):
    old = [{"profile": "old", "source_sha256": "x", "evidence_key": "other"}] * 12
    state = {"config": {"model_calls": 3}, "nodes": {
        "visited_address": node(tmp_path, jobs=old),
        "fresh_other": node(tmp_path, faults={"structural": 9}, source="void f(void) {}\n", sha="s5")}}
    snapshot, selected = queue.project(state, campaign.PROFILES)
    assert selected[0] == "visited_address"
    assert snapshot["work_items"]["visited_address"]["priority"][0] == -1


def test_address_rounds_reach_agentrepair():
    import inspect
    from eval import agentrepair
    assert "address_rounds" in inspect.signature(agentrepair.run).parameters
    assert 'address_rounds=profile.get("address_rounds", 0)' in inspect.getsource(campaign.execute)


def test_relocation_only_residuals_recertify_too(tmp_path):
    only = node(tmp_path, score=99.8, faults={"relocation": 2}, source="void f(void) { g(1.5f); }\n", sha="r1")
    assert queue.next_profile(only, 3, campaign.PROFILES)["name"] == "recertify@" + queue.certificate_digest()
    mixed = node(tmp_path, score=99.8, faults={"relocation": 2, "structural": 1}, source="void f(void) {}\n", sha="r2")
    assert not queue.next_profile(mixed, 3, campaign.PROFILES)["name"].startswith("recertify@")


def test_structural_shapes_run_structural_rewrites_once_per_source(tmp_path):
    source = "void f(A *arg0) {\n    u8 v;\n    arg0->state = 2;\n    v = 2 & 0xFF;\n}\n"
    shaped = node(tmp_path, faults={"structural": 3}, source=source, sha="t1")
    profile = queue.next_profile(shaped, 3, campaign.PROFILES)
    assert profile["name"] == "structural_rewrites" and profile["structural_rounds"] == 3 and not profile["model"]
    ran = node(tmp_path, faults={"structural": 3}, source=source, sha="t1",
               jobs=[{"profile": "structural_rewrites", "source_sha256": "t1"}])
    assert queue.next_profile(ran, 3, campaign.PROFILES)["name"] != "structural_rewrites"
    plain = node(tmp_path, faults={"structural": 3}, source="void f(void) {\n    x = 1;\n}\n", sha="t2")
    assert queue.next_profile(plain, 3, campaign.PROFILES)["name"] != "structural_rewrites"


def test_structural_rounds_reach_agentrepair():
    import inspect
    from eval import agentrepair
    assert "structural_rounds" in inspect.signature(agentrepair.run).parameters
    assert 'structural_rounds=profile.get("structural_rounds", 0)' in inspect.getsource(campaign.execute)


def test_failing_semantic_nodes_revalidate_once_per_environment_code_and_source(tmp_path):
    failing = node(tmp_path, faults={"structural": 9}, source="void f(void) {}\n", sha="v1")
    failing["semantic_validation"] = {"status": "observed_failure", "source_sha256": "v1"}
    profile = queue.next_profile(failing, 3, campaign.PROFILES)
    assert profile["name"] == "revalidate@" + queue.semantic_environment_digest() and not profile["model"]
    failing["jobs"] = [{"profile": profile["name"], "source_sha256": "v1"}]
    assert not queue.next_profile(failing, 3, campaign.PROFILES)["name"].startswith("revalidate@")
    passing = node(tmp_path, faults={"structural": 9}, source="void f(void) {}\n", sha="v2")
    assert not queue.next_profile(passing, 3, campaign.PROFILES)["name"].startswith("revalidate@")
    state = {"config": {"model_calls": 3}, "nodes": {"failing": node(tmp_path, faults={"structural": 9},
             source="void f(void) {}\n", sha="v3", jobs=[{"profile": "old", "source_sha256": "x", "evidence_key": "o"}] * 12)}}
    state["nodes"]["failing"]["semantic_validation"] = {"status": "observed_failure", "source_sha256": "v3"}
    snapshot, _selected = queue.project(state, campaign.PROFILES)
    assert snapshot["work_items"]["failing"]["priority"][0] == -1

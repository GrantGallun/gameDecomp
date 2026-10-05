"""Campaign wiring of the 90+/non-compiling census amendment: stack_layout, named_rodata,
placeholder_recovery, per-source zero-model dedup and the register-dominance tie-break."""
from eval import completion_campaign as campaign
from solver import compile_recovery, regalloc_search, repair_queue as queue


def node(tmp_path, *, score=99.0, faults=None, source="void f(void) {\n    s32 pad;\n}\n", jobs=(), sha="s1",
         compiled=True, first=None):
    path = tmp_path / f"{sha}.c"
    path.write_text(source)
    residual = {"compiled": compiled, "frontend": {"passed": True},
                "faults": faults if faults is not None else {"immediate": 2}}
    if first is not None:
        residual["first_difference"] = first
    if not compiled:
        residual["compiler_error_signature"] = "candidate.c, line 2: Syntax Error"
    return {"status": "pending", "source_sha256": sha, "source": str(path), "attempt_id": 1, "score": score,
            "residual": residual, "semantic_validation": {"status": "observed_pass", "source_sha256": sha},
            "jobs": list(jobs), "instruction_count": 20}


def test_stack_residuals_run_stack_layout_once_per_source(tmp_path):
    profile = queue.next_profile(node(tmp_path, first=["-addiu    sp,sp,-0x28", "+addiu    sp,sp,-0x30"]), 3, campaign.PROFILES)
    assert profile["name"] == "stack_layout" and profile["stack_rounds"] == 4 and not profile["model"]
    ran = node(tmp_path, jobs=[{"profile": "stack_layout", "source_sha256": "s1"}])
    assert queue.next_profile(ran, 3, campaign.PROFILES)["name"] != "stack_layout"
    assert queue.next_profile(node(tmp_path, faults={"register_allocation": 4}, sha="s2"), 3,
                              campaign.PROFILES)["name"] != "stack_layout"


def test_string_literals_with_relocation_faults_run_named_rodata(tmp_path):
    source = 'void f(void) {\n    g("Point");\n}\n'
    profile = queue.next_profile(node(tmp_path, faults={"relocation": 2, "structural": 1}, source=source, sha="r1"),
                                 3, campaign.PROFILES)
    assert profile["name"] == "named_rodata" and profile["address_rounds"] == 3
    low = node(tmp_path, score=80.0, faults={"relocation": 2, "structural": 1}, source=source, sha="r3")
    assert queue.next_profile(low, 3, campaign.PROFILES)["name"] != "named_rodata"
    plain = node(tmp_path, faults={"relocation": 2, "structural": 1}, source="void f(void) {\n    g(1);\n}\n", sha="r2")
    assert queue.next_profile(plain, 3, campaign.PROFILES)["name"] != "named_rodata"


def test_placeholder_sources_get_one_zero_model_recovery_visit(tmp_path):
    source = "? g(?);  /* extern */\nvoid f(void) {\n    g(1);\n}\n"
    blocked = node(tmp_path, compiled=False, faults={}, score=0.0, source=source, sha="p1",
                   jobs=[{"profile": "compile_recovery", "source_sha256": "p1"}] * 2)
    profile = queue.next_profile(blocked, 3, campaign.PROFILES)
    assert profile["name"] == "placeholder_recovery" and not profile["model"] and profile["evidence_key"]
    visited = node(tmp_path, compiled=False, faults={}, score=0.0, source=source, sha="p1",
                   jobs=[{"profile": "placeholder_recovery", "source_sha256": "p1"}])
    assert queue.next_profile(visited, 3, campaign.PROFILES)["name"] != "placeholder_recovery"
    clean = node(tmp_path, compiled=False, faults={}, score=0.0, source="void f(void) {\n    x;\n}\n", sha="p2")
    assert queue.next_profile(clean, 3, campaign.PROFILES)["name"] == "compile_recovery"


def test_zero_model_profiles_are_spent_per_source_across_evidence_keys(tmp_path):
    cycling = node(tmp_path, faults={"structural": 5}, source="void f(void) {\n    x = 1;\n}\n", sha="ninety-c1",
                   jobs=[{"profile": "local_rewrites", "source_sha256": "ninety-c1", "evidence_key": "an-older-key"}])
    assert queue.next_profile(cycling, 3, campaign.PROFILES)["name"] not in ("local_rewrites", "regalloc_search")
    other_source = node(tmp_path, faults={"structural": 5}, source="void f(void) {\n    x = 1;\n}\n", sha="ninety-c2",
                        jobs=[{"profile": "local_rewrites", "source_sha256": "ninety-c1", "evidence_key": "an-older-key"}])
    assert queue.next_profile(other_source, 3, campaign.PROFILES)["name"] == "local_rewrites"


def test_register_dominance_ties_reach_regalloc_search(tmp_path):
    assert regalloc_search.register_dominant({"structural": 2, "register_allocation": 2})
    assert not regalloc_search.register_dominant({"structural": 3, "register_allocation": 2})
    tied = node(tmp_path, faults={"structural": 2, "register_allocation": 2}, source="void f(void) {\n    x = 1;\n}\n", sha="ninety-tie")
    assert queue.next_profile(tied, 3, campaign.PROFILES)["name"] == "regalloc_search"


def test_new_profiles_run_ahead_of_their_visit_band(tmp_path):
    old = [{"profile": "old", "source_sha256": "x", "evidence_key": "other"}] * 12
    state = {"config": {"model_calls": 3}, "nodes": {
        "stack": node(tmp_path, jobs=old, first=["-addiu    sp,sp,-0x28"]),
        "fresh": node(tmp_path, faults={"structural": 9}, source="void f(void) {}\n", sha="s5")}}
    snapshot, selected = queue.project(state, campaign.PROFILES)
    assert selected[0] == "stack" and snapshot["work_items"]["stack"]["priority"][0] == -1


def test_stack_rounds_and_placeholder_stage_are_wired():
    import inspect
    from eval import agentrepair
    assert "stack_rounds" in inspect.signature(agentrepair.run).parameters
    assert 'stack_rounds=profile.get("stack_rounds", 0)' in inspect.getsource(campaign.execute)
    # The placeholder stage now runs inside void_pointer_units.lowered_candidates (draft-lowering follow-up).
    assert "placeholder_declarations.signals" in inspect.getsource(compile_recovery.variants)

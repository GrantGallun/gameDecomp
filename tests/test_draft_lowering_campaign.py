"""Campaign wiring of the draft-lowering follow-up: draft_lowering profile and the compile-recovery hook."""
from eval import completion_campaign as campaign
from solver import compile_recovery, repair_queue as queue

VOID_CURSOR = "void f(void) {\n    void *temp_v0;\n    gPtr = temp_v0 + 8;\n}\n"


def node(tmp_path, source, sha, jobs=()):
    path = tmp_path / f"{sha}.c"
    path.write_text(source)
    return {"status": "pending", "source_sha256": sha, "source": str(path), "attempt_id": 1, "score": 0.0,
            "residual": {"compiled": False, "frontend": {"passed": False},
                         "compiler_error_signature": "candidate.c, line 3: Unacceptable operand of '+'."},
            "semantic_validation": None, "jobs": list(jobs), "instruction_count": 20}


def test_void_cursor_drafts_get_one_draft_lowering_visit(tmp_path):
    fresh = node(tmp_path, VOID_CURSOR, "lowering-1")
    profile = queue.next_profile(fresh, 3, campaign.PROFILES)
    assert profile["name"] == "draft_lowering" and not profile["model"] and profile["evidence_key"]
    visited = node(tmp_path, VOID_CURSOR, "lowering-1", jobs=[{"profile": "draft_lowering", "source_sha256": "lowering-1"}])
    assert queue.next_profile(visited, 3, campaign.PROFILES)["name"] == "compile_recovery"
    plain = node(tmp_path, "void f(void) {\n    void *p;\n    g(p);\n}\n", "lowering-2")
    assert queue.next_profile(plain, 3, campaign.PROFILES)["name"] == "compile_recovery"


def test_placeholder_recovery_still_comes_first_then_lowering(tmp_path):
    source = "? g(?);  /* extern */\n" + VOID_CURSOR
    both = node(tmp_path, source, "lowering-3")
    assert queue.next_profile(both, 3, campaign.PROFILES)["name"] == "placeholder_recovery"
    after = node(tmp_path, source, "lowering-3", jobs=[{"profile": "placeholder_recovery", "source_sha256": "lowering-3"}])
    assert queue.next_profile(after, 3, campaign.PROFILES)["name"] == "draft_lowering"


def test_draft_lowering_runs_ahead_of_its_visit_band(tmp_path):
    old = [{"profile": "old", "source_sha256": "x", "evidence_key": "other"}] * 12
    state = {"config": {"model_calls": 3}, "nodes": {"lowering": node(tmp_path, VOID_CURSOR, "lowering-4", jobs=old)}}
    snapshot, _selected = queue.project(state, campaign.PROFILES)
    assert snapshot["work_items"]["lowering"]["priority"][0] == -1


def test_compile_recovery_uses_lowered_candidates():
    import inspect
    assert "void_pointer_units.lowered_candidates" in inspect.getsource(compile_recovery.variants)

import hashlib
import sqlite3
import pytest


@pytest.mark.parametrize("clean_score,clean_exact,frontend_pass", [(70, False, True), (100, True, False)])
def test_campaign_scores_clean_draft_with_independent_ancestry_and_retains_better_seed(tmp_path, monkeypatch, clean_score, clean_exact, frontend_pass):
    from eval import completion_campaign as campaign
    from solver import binary_type_draft, workspace
    clean = "s32 f(void) { return 4; }"
    retained = "s32 f(void) { return 3; }"
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    monkeypatch.setattr(workspace, "bootstrap", lambda *a: tmp_path)
    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(campaign.agentrepair, "_source_for_attempt", lambda *a: retained)
    monkeypatch.setattr(binary_type_draft, "variants", lambda *a: (
        [("binary-types:ordinary:1", clean)], [{"status": "generated", "label": "binary-types:ordinary:1",
          "source_sha256": hashlib.sha256(clean.encode()).hexdigest(), "evidence": {"elf_sha256": "abc"}}]))
    monkeypatch.setattr(campaign.project_headers, "preflight_variants", lambda *a: [])
    calls = []
    def score(ws, repo, tag, source, **kwargs):
        calls.append((source, kwargs))
        return workspace.Attempt(True, clean_score if source == clean else 95,
                                 clean_exact if source == clean else False, "", "", "", len(calls),
                                 frontend={"passed": frontend_pass if source == clean else True})
    monkeypatch.setattr(workspace, "score", score)
    measured = campaign._intake(repo=tmp_path, db=db, function="f", node={"seed_attempt_id": 42},
                               out=tmp_path / "result.json", binary_type_only=True)
    assert measured["source_sha256"] == hashlib.sha256(retained.encode()).hexdigest()
    clean_calls = [kw for source, kw in calls if source == clean]
    assert len(clean_calls) == 1
    assert clean_calls[0]["parent_attempt_id"] is None
    assert clean_calls[0]["extra"]["assistance_tier"] == "source-independent"
    assert clean_calls[0]["extra"]["binary_type_context"][0]["evidence"]["elf_sha256"] == "abc"
    assert [kw["parent_attempt_id"] for source, kw in calls if source == retained] == [42]


def test_clean_intake_never_uses_base_or_header_adapters_on_binary_branch(tmp_path, monkeypatch):
    from eval import completion_campaign as campaign
    from solver import binary_type_draft, workspace
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    (tmp_path / "base.c").write_text("void f(void) { reference_answer(); }")
    monkeypatch.setattr(workspace, "bootstrap", lambda *a: tmp_path)
    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(binary_type_draft, "variants", lambda *a: ([], [{"status": "declined", "reason": "missing facts"}]))
    def forbidden(*a, **k):
        raise AssertionError("clean route used assisted context")
    monkeypatch.setattr(campaign.m2c_context, "seed_variants", forbidden)
    monkeypatch.setattr(campaign.project_headers, "preflight_variants", forbidden)
    result = campaign._intake(repo=tmp_path, db=db, function="f", node={}, out=tmp_path / "result.json",
                             binary_type_only=True)
    assert result["status"] == "parked"
    assert result["blocker"]["context"][0]["reason"] == "missing facts"


def test_binary_type_profile_reopens_once_when_generator_changes(tmp_path, monkeypatch):
    from solver import binary_type_draft, repair_queue
    src = tmp_path / "f.c"
    src.write_text("void f(void) {}")
    node = {"status": "pending", "source": str(src), "source_sha256": "same-source", "jobs": [{"profile": "compile_recovery"}] * 2,
            "residual": {"compiled": False, "frontend": {"passed": False}}}
    node["jobs"] = [{"profile": "compile_recovery", "evidence_key": repair_queue.evidence_key(node)}]
    monkeypatch.setattr(binary_type_draft, "code_digest", lambda: "revision-one")
    first = repair_queue.next_profile(node, 3, [])
    assert first["binary_types"] is True and first["model"] is False
    node["jobs"].append({"profile": first["name"], "source_sha256": "same-source", "evidence_key": first["evidence_key"]})
    again = repair_queue.next_profile(node, 3, [])
    assert again is None or not again.get("binary_types")
    monkeypatch.setattr(binary_type_draft, "code_digest", lambda: "revision-two")
    changed = repair_queue.next_profile(node, 3, [])
    assert changed["binary_types"] is True and changed["name"] != first["name"]


def test_binary_type_profile_never_reopens_exact_or_parked_nodes(monkeypatch):
    from solver import repair_queue
    for status in ("object_exact", "integrated", "function_exact_pending_integration", "parked"):
        assert repair_queue.next_profile({"status": status}, 3, []) is None


def test_binary_retry_preserves_source_bound_validation_and_checks_incumbent(tmp_path, monkeypatch):
    import pytest
    from eval import completion_campaign as campaign
    src = tmp_path / "f.c"
    src.write_text("void f(void) {}")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    semantic = {"source_sha256": sha, "status": "observed_pass", "panel_sha256": "panel"}
    node = {"source": str(src), "source_sha256": sha, "attempt_id": 42, "semantic_validation": semantic}
    monkeypatch.setattr(campaign, "_intake", lambda **kw: {"source_sha256": sha})
    kwargs = dict(repo=tmp_path, db=tmp_path / "db", function="f", node=node,
                  profile={"name": "binary_types@1", "binary_types": True}, config={}, out=tmp_path / "r.json")
    assert campaign.execute(**kwargs)["semantic_validation"] == semantic
    monkeypatch.setattr(campaign, "_intake", lambda **kw: {"source_sha256": "new-source"})
    assert "semantic_validation" not in campaign.execute(**kwargs)
    src.write_text("void f(void) { tampered(); }")
    with pytest.raises(ValueError, match="changed outside controller"):
        campaign.execute(**kwargs)


def test_clean_placeholder_child_keeps_binary_evidence(tmp_path, monkeypatch):
    from eval import completion_campaign as campaign
    from solver import binary_type_draft, workspace
    db = tmp_path / "db.sqlite"
    sqlite3.connect(db).close()
    raw = "void f(? x) {}"
    raw_sha = hashlib.sha256(raw.encode()).hexdigest()
    monkeypatch.setattr(workspace, "bootstrap", lambda *a: tmp_path)
    monkeypatch.setattr(workspace, "target_asm", lambda *a: "glabel f")
    monkeypatch.setattr(binary_type_draft, "variants", lambda *a: (
        [("binary-types:ordinary:1", raw)], [{"label": "binary-types:ordinary:1", "status": "generated",
        "source_sha256": raw_sha, "evidence": {"elf_sha256": "binary"}}]))
    calls = []
    def score(ws, repo, tag, source, **kw):
        calls.append((source, kw))
        return workspace.Attempt(False, 0, False, "", "syntax error", "", len(calls))
    monkeypatch.setattr(workspace, "score", score)
    campaign._intake(repo=tmp_path, db=db, function="f", node={}, out=tmp_path / "r.json", binary_type_only=True)
    assert len(calls) == 2
    source, kw = calls[1]
    receipt = kw["extra"]["binary_type_context"][0]
    assert kw["parent_attempt_id"] == 1
    assert receipt["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert receipt["derived_from_sha256"] == raw_sha
    assert receipt["evidence"]["elf_sha256"] == "binary"


def test_binary_profile_reopens_after_pinned_elf_amendment(tmp_path):
    from solver import repair_queue
    source = tmp_path / "f.c"
    source.write_text("void f(void) {}")
    (tmp_path / "build").mkdir()
    elf = tmp_path / "build" / "game.elf"
    elf.write_bytes(b"target")
    node = {"status": "pending", "source": str(source), "source_sha256": "same", "jobs": [],
            "residual": {"compiled": False, "frontend": {"passed": False}}}
    node["jobs"] = [{"profile": "compile_recovery", "evidence_key": repair_queue.evidence_key(node)}]
    state = {"config": {"repo": str(tmp_path), "model_calls": 0}, "nodes": {"f": node},
             "pins": {str(elf.resolve()): "first-binary"}}
    _, (_, first) = repair_queue.project(state, [])
    node["jobs"].append({"profile": first["name"]})
    _, same = repair_queue.project(state, [])
    assert same is None
    state["pins"][str(elf.resolve())] = "amended-binary"
    _, (_, amended) = repair_queue.project(state, [])
    assert amended["binary_types"] and amended["name"] != first["name"]

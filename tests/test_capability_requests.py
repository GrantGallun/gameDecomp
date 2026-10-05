import copy

import pytest

from solver import capability_requests, repair_queue
from solver.evidence_schedule import fingerprint


def project(tmp_path):
    for name in ("solver", "tests"):
        (tmp_path / name).mkdir(parents=True)
    for name in ("solver/repair.py", "tests/test_reproduce.py",
                 "tests/test_regression.py", "tests/test_transfer.py"):
        (tmp_path / name).write_text("def test_placeholder(): pass\n")
    return tmp_path


def nodes(*names):
    return {name: {"status": "parked", "source_sha256": name,
                   "blocker": {"status": "object_postprocessing_backend_required",
                               "evidence": {"target": "build/src/f.o", "postprocess": "trim",
                                            "makefile_sha256": "m"}}} for name in names}


def payload(key):
    return {"issue_key": key, "hypothesis": "Repair shared object postprocessing",
            "modules": ["solver/repair.py"],
            "reproduce": ["tests/test_reproduce.py::test_placeholder"],
            "regression": ["tests/test_regression.py::test_placeholder"],
            "transfer": ["tests/test_transfer.py::test_placeholder"]}


def test_propose_grounds_model_request_in_shared_issue_and_existing_tests(tmp_path):
    root = project(tmp_path)
    issues = repair_queue.shared_issues(nodes("a", "b"))
    key, issue = next(iter(issues.items()))

    task = capability_requests.propose(payload(key), issues=issues, project=root, function="a")

    assert task["issue_key"] == key
    assert task["evidence"] == issue["identity"]
    assert task["model_proposed"] is True
    assert task["training_ineligible"] is True
    assert task["modules"] == ["solver/repair.py"]


def test_propose_rejects_model_identity_and_unsafe_paths(tmp_path):
    root = project(tmp_path)
    issues = repair_queue.shared_issues(nodes("a", "b"))
    key = next(iter(issues))
    with pytest.raises(ValueError, match="model.*evidence"):
        capability_requests.propose({**payload(key), "evidence": {"target": "fake"}},
                                    issues=issues, project=root, function="a")
    with pytest.raises(ValueError, match="verifiers"):
        capability_requests.propose({**payload(key), "modules": ["eval/completion_campaign.py"]},
                                    issues=issues, project=root, function="a")
    with pytest.raises(ValueError, match="test selector"):
        capability_requests.propose({**payload(key), "regression": ["tests/test_missing.py"]},
                                    issues=issues, project=root, function="a")


@pytest.mark.parametrize('field,bad', [
    ('issue_key', []), ('issue_key', None),
    ('modules', None), ('modules', 'solver/repair.py'), ('modules', [None]),
    ('modules', [['solver/repair.py']]),
    ('reproduce', None), ('reproduce', 'tests/test_reproduce.py::test_placeholder'),
    ('regression', [None]), ('transfer', [['tests/test_transfer.py::test_placeholder']]),
])
def test_propose_malformed_json_task_declines_as_value_error(tmp_path, field, bad):
    root = project(tmp_path)
    issues = repair_queue.shared_issues(nodes('a', 'b'))
    key = next(iter(issues))
    request = payload(key)
    request[field] = bad
    with pytest.raises(ValueError):
        capability_requests.propose(request, issues=issues, project=root, function='a')


def test_propose_requires_repeated_issue_affecting_current_function(tmp_path):
    root = project(tmp_path)
    issues = repair_queue.shared_issues(nodes("a", "b"))
    key = next(iter(issues))
    with pytest.raises(ValueError, match="unrelated"):
        capability_requests.propose(payload(key), issues=issues, project=root, function="c")
    singleton = repair_queue.shared_issues(nodes("a"))
    with pytest.raises(ValueError, match="single consumer"):
        capability_requests.propose(payload(next(iter(singleton))), issues=singleton,
                                    project=root, function="a")


def test_ingest_rechecks_current_issue_and_deduplicates_without_touching_config(tmp_path):
    root = project(tmp_path)
    state = {"config": {"model_calls": 2}, "nodes": nodes("a", "b")}
    config_before = copy.deepcopy(state["config"])
    key, issue = next(iter(repair_queue.shared_issues(state["nodes"]).items()))
    task = capability_requests.propose(payload(key), issues={key: issue}, project=root, function="a")

    first = capability_requests.ingest(state, "a", [task], root)
    second = capability_requests.ingest(state, "a", [task], root)

    assert any("accepted" in line for line in first)
    assert any("duplicate" in line for line in second)
    assert list(state["capability_tasks"]) == [key]
    assert state["capability_tasks"][key] == task
    assert state["config"] == config_before


def test_ingest_rejects_stale_or_drifted_task_without_mutating_state(tmp_path):
    root = project(tmp_path)
    state = {"config": {}, "nodes": nodes("a", "b")}
    key, issue = next(iter(repair_queue.shared_issues(state["nodes"]).items()))
    task = capability_requests.propose(payload(key), issues={key: issue}, project=root, function="a")
    bad = {**task, "evidence": {"target": "wrong"}}
    notes = capability_requests.ingest(state, "a", [bad], root)
    assert any("evidence" in line for line in notes)
    assert "capability_tasks" not in state

    capability_requests.ingest(state, "a", [task], root)
    drifted = {**task, "hypothesis": "a new story for another budget"}
    notes = capability_requests.ingest(state, "a", [drifted], root)
    assert any("drift" in line for line in notes)
    assert state["capability_tasks"][key] == task


def test_ingest_copies_request_so_later_model_mutation_cannot_reset_budget(tmp_path):
    root = project(tmp_path)
    state = {"config": {}, "nodes": nodes("a", "b")}
    key, issue = next(iter(repair_queue.shared_issues(state["nodes"]).items()))
    task = capability_requests.propose(payload(key), issues={key: issue}, project=root, function="a")
    capability_requests.ingest(state, "a", [task], root)

    task["modules"].append("solver/another.py")
    task["evidence"]["target"] = "changed"

    assert state["capability_tasks"][key]["modules"] == ["solver/repair.py"]
    assert state["capability_tasks"][key]["evidence"]["target"] == "build/src/f.o"
    assert issue["identity"]["target"] == "build/src/f.o"


def test_already_attempted_request_does_not_reenter_queue(tmp_path):
    root = project(tmp_path)
    state = {"config": {}, "nodes": nodes("a", "b")}
    key, issue = next(iter(repair_queue.shared_issues(state["nodes"]).items()))
    task = capability_requests.propose(payload(key), issues={key: issue}, project=root, function="a")
    state["nodes"]["b"]["jobs"] = [{"profile": "capability_repair", "evidence_key": fingerprint(task)}]

    notes = capability_requests.ingest(state, "a", [task], root)

    assert any("already attempted" in line for line in notes)
    assert "capability_tasks" not in state


def test_context_lists_bounded_relevant_issues_and_schema():
    issues = repair_queue.shared_issues(nodes("a", "b"))
    separate = nodes("c")
    separate["c"]["blocker"]["evidence"]["postprocess"] = "other"
    unrelated = repair_queue.shared_issues(separate)
    issues.update(unrelated)
    result = capability_requests.context(issues, "a", max_items=1)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["consumer_count"] == 2
    assert result["issues"][0]["evidence"]
    assert set(result["task_schema"]) == {"issue_key", "hypothesis", "modules",
                                           "reproduce", "regression", "transfer"}
    assert all("c" not in item["affected_functions"] for item in result["issues"])


def test_catalog_returns_matching_module_apis_and_test_selectors_without_bodies(tmp_path):
    root = project(tmp_path)
    (root / "solver" / "repair.py").write_text(
        'SECRET_C = "int heldout(void) { return 99; }"\n'
        'def repair_loop():\n    return SECRET_C\n'
        'class RepairPlan:\n    pass\n')
    (root / "tests" / "test_reproduce.py").write_text(
        'def test_repair_loop():\n    secret = "int heldout(void) { return 99; }"\n'
        'class TestRepair:\n    def test_repair_plan(self):\n        assert True\n')

    result = capability_requests.catalog(root, "repair")

    assert {item["path"] for item in result["modules"]} == {"solver/repair.py"}
    assert result["modules"][0]["api"] == ["repair_loop", "RepairPlan"]
    assert result["tests"][0]["selectors"] == [
        "tests/test_reproduce.py::test_repair_loop",
        "tests/test_reproduce.py::TestRepair::test_repair_plan",
    ]
    assert "heldout" not in str(result)
    assert "return 99" not in str(result)


def test_catalog_filters_protected_binary_and_unrelated_paths(tmp_path):
    root = project(tmp_path)
    (root / "solver" / "workspace.py").write_text("def workspace_repair(): pass\n")
    (root / "solver" / "binary.py").write_bytes(b"def repair(): pass\x00")
    (root / "solver" / "other.py").write_text("def unrelated(): pass\n")
    (root / "tests" / "test_reproduce.py").write_text("def test_repair(): pass\n")

    result = capability_requests.catalog(root, "repair", limit=1)

    assert len(result["modules"]) <= 1 and len(result["tests"]) <= 1
    assert all(item["path"] not in {"solver/workspace.py", "solver/binary.py"}
               for item in result["modules"])
    assert capability_requests.catalog(root, "never_a_real_name")["modules"] == []
    with pytest.raises(ValueError, match="query"):
        capability_requests.catalog(root, "x" * 121)


def test_catalog_skips_symlink_that_escapes_project(tmp_path):
    root = project(tmp_path / "project")
    outside = tmp_path / "outside.py"
    outside.write_text("def escaped_repair(): pass\n")
    try:
        (root / "solver" / "escaped.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")

    assert capability_requests.catalog(root, "escaped")["modules"] == []

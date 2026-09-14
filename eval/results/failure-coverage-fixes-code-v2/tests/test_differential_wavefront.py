from eval import differential_wavefront as wavefront
import json
import pytest


def _coverage(status="complete"):
    return {"status": status,
            "coverage_model_version": wavefront.differential.COVERAGE_MODEL_VERSION}


def test_old_coverage_completeness_requires_revalidation():
    old = {"status": "complete", "covered_instruction_count": 15}
    checked = wavefront.current_coverage(old)
    assert checked["status"] == "partial"
    assert checked["stale_coverage"]
    assert old["status"] == "complete"
    assert wavefront.current_coverage(_coverage())["status"] == "complete"


def _node(*, compiled=True, exact=False, statuses=None, cases=None):
    return {
        "attempt": {"compiled": compiled, "exact": exact},
        "differential": {"statuses": statuses or {"failed": 1}},
        "candidate_exploration": {"selected_cases": cases or [{
            "name": "case",
            "seed": 7,
            "player_writes": [[16, 4, 3]],
            "global_writes": [["gValue", 2, 9]],
            "entry_registers": [["a1", 5]],
        }]},
    }


def test_cases_from_node_restores_immutable_differential_cases():
    cases = wavefront.cases_from_node(_node())

    assert len(cases) == 1
    assert cases[0].player_writes == ((16, 4, 3),)
    assert cases[0].global_writes == (("gValue", 2, 9),)
    assert cases[0].entry_registers == (("a1", 5),)


def test_route_admits_partial_coverage_when_a_conclusive_case_exists():
    eligible, reason = wavefront.route_node(_node(statuses={"failed": 1}))

    assert eligible
    assert reason == "differential repair eligible"


def test_route_rejects_noncompiling_exact_and_inconclusive_nodes():
    assert not wavefront.route_node(_node(compiled=False))[0]
    assert not wavefront.route_node(_node(exact=True))[0]
    assert not wavefront.route_node(_node(statuses={"inconclusive": 1}))[0]


def test_compiler_lane_does_not_require_emulator_support():
    node = _node(statuses={"inconclusive": 2})
    node['candidate_exploration']['selected_cases'] = []
    assert wavefront.repair_lane(node)[0] == 'exactness_repair'
    assert wavefront.repair_lane(_node(), exactness_only=True)[0] == 'exactness_repair'
    assert wavefront.repair_lane(_node())[0] == 'differential_repair'
    assert wavefront.repair_lane(_node(compiled=False), exactness_only=True)[0] == 'skip'
    assert wavefront.repair_lane(_node(exact=True), exactness_only=True)[0] == 'skip'


def test_integration_queue_requires_source_bound_certificate(tmp_path):
    import hashlib
    source = tmp_path / 'best.c'
    source.write_text('int f(void) { return 1; }')
    row = {'function': 'f', 'exact': True, 'best_source': str(source),
           'best_attempt_id': 9, 'verification': {'exact': True, 'candidate_source_sha256': 'stale'}}
    receipt = {'kind': wavefront.WAVE_KIND, 'nodes': [row]}
    wavefront._atomic_json(tmp_path / 'wave.json', receipt)
    assert receipt['integration_queue'] == []
    row['verification']['candidate_source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    wavefront._atomic_json(tmp_path / 'wave.json', receipt)
    assert len(receipt['integration_queue']) == 1
    assert receipt['whole_rom_verified'] is False


@pytest.mark.parametrize('exactness_only', [False, True])
def test_wave_dispatches_compiler_lane_without_semantic_claims(tmp_path, monkeypatch, exactness_only):
    node = _node(statuses={'passed': 9} if exactness_only else {'inconclusive': 9})
    node.update(function='f', dag_level=0, panel_dependencies=[],
                semantic_authoritative=True, combined_target_coverage=_coverage(),
                combined_candidate_coverage=_coverage())
    node['attempt']['attempt_id'] = 1
    census = tmp_path / 'census.json'
    census.write_text(json.dumps({'kind': wavefront.CENSUS_KIND, 'run_id': 'root',
                                 'dag': {'nodes': [node]}}))
    monkeypatch.setattr(wavefront.agentrepair, '_refuse_frozen_heldout', lambda *args: None)
    monkeypatch.setattr(wavefront.agentrepair, '_source_for_attempt', lambda *args: 'void f(void) {}')
    def exact_worker(**kwargs):
        assert kwargs['max_calls'] == 2
        assert kwargs['source_parent_attempt_id'] == 1
        return {'run_id': 'exact-run', 'result': {'best_attempt_id': 2, 'exact': False,
            'best_residual': {'weighted_progress_score': 96.0}, 'frontier': [{'attempt_id': 2}],
            'calls_attempted': 2, 'compiling_children': 1, 'charged_tokens': 20}}
    monkeypatch.setattr(wavefront.agentrepair, 'run', exact_worker)
    monkeypatch.setattr(wavefront.repair, 'run', lambda **kwargs: pytest.fail('wrong worker'))
    result = wavefront.run(repo=tmp_path, db=tmp_path / 'test.db', sets=tmp_path / 'sets',
        census_path=census, output=tmp_path / 'wave.json', model='unused', endpoint='unused',
        rounds=2, timeout=1, think='low', num_thread=1, temperature=0,
        diagnosis_num_predict=1, patch_num_predict=1, patch_retries=0, compiler_retries=0,
        max_stalls=1, seed=1, cache_dir=None, m2c_preflight=False, exactness_only=exactness_only)
    assert result['aggregate']['exactness_repair_nodes'] == 1
    assert result['aggregate']['observed_semantic_pass'] == 0
    assert result['aggregate']['semantic_settled'] == 0
    assert result['nodes'][0]['retained_frontier'] == [{'attempt_id': 2}]
    assert result['nodes'][0]['failure_category'] == 'byte_residual'


@pytest.mark.parametrize('outcome', ['failed', 'exact', 'repair'])
def test_noncompiling_root_gets_intake_before_semantic_eligibility(tmp_path, monkeypatch, outcome):
    node = _node(compiled=False)
    node.update(function='f', dag_level=0, panel_dependencies=[], stop_stage='compilation')
    node['attempt']['attempt_id'] = 1
    census = tmp_path / 'census.json'
    census.write_text(json.dumps({'kind': wavefront.CENSUS_KIND, 'run_id': 'root',
                                 'dag': {'nodes': [node]}}))
    monkeypatch.setattr(wavefront.agentrepair, '_refuse_frozen_heldout', lambda *args: None)
    source_ids, calls = [], []

    def intake(**kwargs):
        calls.append('intake')
        assert kwargs['node']['attempt']['compiled'] is False
        if outcome == 'failed':
            return {'status': 'compile_failed'}
        fresh = _node(compiled=True, exact=outcome == 'exact')
        fresh.update(function='f', dag_level=0, panel_dependencies=[],
                     stop_stage='semantic_repair', combined_target_coverage=_coverage(),
                     combined_candidate_coverage=_coverage())
        fresh['attempt']['attempt_id'] = 2
        return {'status': 'compiled', 'node': fresh, 'census': str(tmp_path / 'fresh.json'),
                'exact': outcome == 'exact', 'best_attempt_id': 2, 'score': 100,
                'run_id': 'intake-run'}

    def source(conn, aid, name):
        source_ids.append(aid)
        return 'void f(void) {}'

    def repair(**kwargs):
        calls.append('repair')
        assert kwargs['source_parent_attempt_id'] == 2
        return {'status': 'complete', 'run_id': 'repair-run', 'iterations': [],
                'termination_reason': 'round limit', 'charged_tokens': 0,
                'result': {'semantic_cases_passed': 0, 'all_semantic_cases_passed': False,
                           'exact': False, 'best_attempt': {'score': 50, 'attempt_id': 3}}}

    monkeypatch.setattr(wavefront.compile_intake, 'run', intake)
    monkeypatch.setattr(wavefront.agentrepair, '_source_for_attempt', source)
    monkeypatch.setattr(wavefront.repair, 'run', repair)
    result = wavefront.run(repo=tmp_path, db=tmp_path / 'test.db', sets=tmp_path / 'sets',
        census_path=census, output=tmp_path / 'wave.json', model='unused', endpoint='unused',
        rounds=0, timeout=1, think='low', num_thread=1, temperature=0,
        diagnosis_num_predict=1, patch_num_predict=1, patch_retries=0, compiler_retries=0,
        max_stalls=1, seed=1, cache_dir=None, m2c_preflight=False)
    assert result['aggregate']['compile_intake_nodes'] == 1
    assert result['aggregate']['compile_intake_failures'] == (outcome == 'failed')
    assert calls == (['intake', 'repair'] if outcome == 'repair' else ['intake'])
    assert source_ids == ([2] if outcome == 'repair' else [])
    if outcome == 'exact':
        assert result['aggregate']['exact'] == 1
        assert result['nodes'][0]['route'] == 'compile_intake'


@pytest.mark.parametrize("discover_failure", [False, True])
def test_wavefront_dispatches_census_order_and_retains_coverage_debt(
        tmp_path, monkeypatch, discover_failure):
    rows = []
    for index, (name, level, coverage) in enumerate((
            ("smallLeaf", 0, "complete"),
            ("largeLeaf", 0, "partial"),
            ("caller", 1, "complete")), start=1):
        node = _node()
        if discover_failure and name == "largeLeaf":
            node["differential"]["statuses"] = {"passed": 1}
        node.update({
            "function": name,
            "dag_level": level,
            "panel_dependencies": [],
            "stop_stage": "semantic_repair",
            "semantic_authoritative": False,
            "combined_target_coverage": _coverage(coverage),
            "combined_candidate_coverage": _coverage(coverage),
            "abi": {"provisional_call_arities": {},
                    "function": {"return_registers": []}},
        })
        node["attempt"]["attempt_id"] = index
        rows.append(node)
    census = tmp_path / "census.json"
    census.write_text(json.dumps({
        "kind": wavefront.CENSUS_KIND,
        "run_id": "census-run",
        "dag": {"nodes": rows},
    }), encoding="utf-8")
    calls = []
    discovered_case = {"name": "new-allocation-path", "seed": 8}

    def fake_coverage(**kwargs):
        assert kwargs["function"] == "largeLeaf"
        return {"panel": {"selected_cases": [discovered_case]},
                "candidates": [{"attempt": {"compiled": True, "attempt_id": 200},
                                "differential": {"all": {"failed": 1},
                                                 "target_coverage": _coverage("partial"),
                                                 "candidate_coverage": _coverage("partial")}}]}

    monkeypatch.setattr(wavefront.coverage_worker, "run", fake_coverage)

    monkeypatch.setattr(
        wavefront.agentrepair, "_refuse_frozen_heldout",
        lambda _sets, _function: None)
    monkeypatch.setattr(
        wavefront.agentrepair, "_source_for_attempt",
        lambda _conn, _attempt_id, function: f"void {function}(void) {{}}")

    def fake_repair(**kwargs):
        calls.append(kwargs["function"])
        if discover_failure and kwargs["function"] == "largeLeaf":
            assert kwargs["cases"][0].name == "new-allocation-path"
            assert kwargs["source_parent_attempt_id"] == 200
        return {
            "status": "complete", "run_id": "child-" + kwargs["function"],
            "iterations": [{}], "termination_reason": "round limit",
            "charged_tokens": 10,
            "result": {
                "semantic_cases_passed": 0,
                "all_semantic_cases_passed": False,
                "exact": False,
                "best_attempt": {"score": 50.0, "attempt_id": 100},
            },
        }

    monkeypatch.setattr(wavefront.repair, "run", fake_repair)
    result = wavefront.run(
        repo=tmp_path, db=tmp_path / "attempts.sqlite",
        sets=tmp_path / "sets", census_path=census,
        output=tmp_path / "wave.json", model="fake", endpoint="fake",
        rounds=1, timeout=1, think="low", num_thread=1,
        temperature=0.0, diagnosis_num_predict=1, patch_num_predict=1,
        patch_retries=0, compiler_retries=0, max_stalls=1, seed=1,
        cache_dir=None, m2c_preflight=False)

    assert calls == ["smallLeaf", "largeLeaf", "caller"]
    assert result["nodes"][1]["route"] == "differential_repair"
    assert result["nodes"][1]["charged_tokens"] == 10
    assert not result["nodes"][1]["target_coverage_complete"]
    assert result["status"] == "complete"

    calls.clear()
    subset = wavefront.run(
        repo=tmp_path, db=tmp_path / "attempts.sqlite",
        sets=tmp_path / "sets", census_path=census,
        output=tmp_path / "wave-subset.json", model="fake", endpoint="fake",
        rounds=1, timeout=1, think="low", num_thread=1,
        temperature=0.0, diagnosis_num_predict=1, patch_num_predict=1,
        patch_retries=0, compiler_retries=0, max_stalls=1, seed=1,
        cache_dir=None, functions=("largeLeaf", "caller"),
        m2c_preflight=False)

    assert calls == ["largeLeaf", "caller"]
    assert [row["function"] for row in subset["nodes"]] == [
        "largeLeaf", "caller"]


def test_continuation_wave_starts_from_parent_retained_best(
        tmp_path, monkeypatch):
    node = _node()
    node.update({
        "function": "leaf", "dag_level": 0, "panel_dependencies": [],
        "stop_stage": "semantic_repair", "semantic_authoritative": False,
        "combined_target_coverage": _coverage(),
        "combined_candidate_coverage": _coverage(),
        "abi": {"provisional_call_arities": {},
                "function": {"return_registers": []}},
    })
    node["attempt"]["attempt_id"] = 1
    census = tmp_path / "census.json"
    census.write_text(json.dumps({
        "kind": wavefront.CENSUS_KIND, "run_id": "census-run",
        "dag": {"nodes": [node]},
    }), encoding="utf-8")
    parent = tmp_path / "parent.json"
    parent.write_text(json.dumps({
        "kind": wavefront.WAVE_KIND, "run_id": "first-wave",
        "source_census_run_id": "census-run",
        "nodes": [{"function": "leaf", "best_attempt_id": 99,
                   "best_score": 72.5}],
    }), encoding="utf-8")
    source_ids = []
    repair_parent_ids = []

    monkeypatch.setattr(
        wavefront.agentrepair, "_refuse_frozen_heldout",
        lambda _sets, _function: None)

    def fake_source(_conn, attempt_id, function):
        source_ids.append(attempt_id)
        return f"void {function}(void) {{}}"

    def fake_repair(**kwargs):
        repair_parent_ids.append(kwargs["source_parent_attempt_id"])
        return {
            "status": "complete", "run_id": "continued-leaf",
            "iterations": [], "termination_reason": "round limit",
            "charged_tokens": 0,
            "result": {
                "semantic_cases_passed": 0,
                "all_semantic_cases_passed": False,
                "exact": False,
                "best_attempt": {"score": 72.5, "attempt_id": 99},
            },
        }

    monkeypatch.setattr(wavefront.agentrepair, "_source_for_attempt", fake_source)
    monkeypatch.setattr(wavefront.repair, "run", fake_repair)
    result = wavefront.run(
        repo=tmp_path, db=tmp_path / "attempts.sqlite",
        sets=tmp_path / "sets", census_path=census,
        output=tmp_path / "continued.json", model="fake", endpoint="fake",
        rounds=1, timeout=1, think="low", num_thread=1,
        temperature=0.0, diagnosis_num_predict=1, patch_num_predict=1,
        patch_retries=0, compiler_retries=0, max_stalls=1, seed=1,
        cache_dir=None, parent_wave_path=parent, m2c_preflight=False)

    assert source_ids == [99]
    assert repair_parent_ids == [99]
    assert result["parent_wave_run_id"] == "first-wave"
    assert result["nodes"][0]["source_origin"] == "parent-wave-best"


def test_m2c_semantic_seed_short_circuits_model_repair(tmp_path, monkeypatch):
    node = _node()
    node.update({
        "function": "leaf", "dag_level": 0, "panel_dependencies": [],
        "stop_stage": "semantic_repair", "semantic_authoritative": False,
        "combined_target_coverage": _coverage(),
        "combined_candidate_coverage": _coverage(),
        "abi": {"provisional_call_arities": {},
                "function": {"return_registers": []}},
    })
    node["attempt"]["attempt_id"] = 7
    census = tmp_path / "census.json"
    census.write_text(json.dumps({
        "kind": wavefront.CENSUS_KIND, "run_id": "census-run",
        "dag": {"nodes": [node]},
    }), encoding="utf-8")
    monkeypatch.setattr(
        wavefront.agentrepair, "_refuse_frozen_heldout",
        lambda _sets, _function: None)
    monkeypatch.setattr(
        wavefront.agentrepair, "_source_for_attempt",
        lambda _conn, _attempt_id, _function: "void leaf(void) {}")

    def fake_seed(**kwargs):
        assert kwargs["source_parent_attempt_id"] == 7
        kwargs["best_source_out"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["best_source_out"].write_text(
            "void leaf(void) {}", encoding="utf-8")
        return {
            "status": "complete", "run_id": "seed-run",
            "result": {
                "compiled": True, "exact": False,
                "all_semantic_cases_passed": True,
                "attempt": {"attempt_id": 8, "score": 96.0},
                "differential": {"all": {
                    "passed": 12, "failed": 0, "inconclusive": 0},
                    "target_coverage": {"status": "complete"},
                    "candidate_coverage": {"status": "complete"}},
            },
        }

    monkeypatch.setattr(wavefront.semantic_seed, "run", fake_seed)
    monkeypatch.setattr(
        wavefront.repair, "run",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("model repair should not run")))

    result = wavefront.run(
        repo=tmp_path, db=tmp_path / "attempts.sqlite",
        sets=tmp_path / "sets", census_path=census,
        output=tmp_path / "wave.json", model="fake", endpoint="fake",
        rounds=1, timeout=1, think="low", num_thread=1,
        temperature=0.0, diagnosis_num_predict=1, patch_num_predict=1,
        patch_retries=0, compiler_retries=0, max_stalls=1, seed=1,
        cache_dir=None, m2c_preflight=True, m2c_stress_cases=12)

    assert result["nodes"][0]["route"] == "m2c_semantic_seed"
    assert result["nodes"][0]["semantic_cases_passed"] == 12
    assert result["nodes"][0]["charged_tokens"] == 0
    assert result["nodes"][0]["all_observed_semantic_cases_passed"]
    assert not result["nodes"][0]["semantic_settled"]
    assert "observational non-leaf" in result["nodes"][0][
        "termination_reason"]
    assert result["aggregate"]["m2c_semantic_seed_nodes"] == 1
    assert result["aggregate"]["repair_nodes"] == 0

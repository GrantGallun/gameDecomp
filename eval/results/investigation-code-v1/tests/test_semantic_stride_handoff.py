import pytest
from solver import modelrepair, workspace


@pytest.mark.parametrize('semantic_status, expected', [
    ('observed_failure', 1), ('observed_pass', 0), ('unavailable', 0), ('inconclusive', 0)])
@pytest.mark.parametrize('recovered_child', [False, True])
def test_compiled_recovery_state_gets_bounded_stride_hypothesis(monkeypatch, tmp_path, semantic_status, expected, recovered_child):
    source = 'void f(void) {\n    s32 *p;\n    p = get();\n    p += 4;\n    use(p);\n}'
    root = workspace.Attempt(True, 70, False, '-addiu v0,v0,4\n', '', '', 7,
                             frontend={'passed': True})
    child = workspace.Attempt(True, 71, False, '', '', '', 8, frontend={'passed': True})
    recorded, evaluated = [], []
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel f\njr ra\nnop')
    def score(*args, **kwargs):
        recorded.append((args[3], kwargs))
        return child
    def semantic(state):
        evaluated.append(state.source)
        return {'status': semantic_status if state.source == source else 'observed_pass'}
    monkeypatch.setattr(workspace, 'score', score)
    initial = (modelrepair.CandidateState(source, root, tmp_path/'recovered.o'),) if recovered_child else ()
    baseline = workspace.Attempt(False, 0, False, '', '', '', 6) if recovered_child else root
    result = modelrepair.search(tmp_path, 'f', 'void f(void) {}' if recovered_child else source,
        tmp_path, model='test', endpoint='none', base_attempt=baseline,
        initial_states=initial, resilient=True, max_calls=0, semantic_evaluator=semantic)
    assert result.calls_attempted == 0
    assert len(recorded) == expected
    if expected:
        code, receipt = recorded[0]
        assert '(unsigned char *)p + 4' in code
        assert receipt['parent_attempt_id'] == 7
        assert receipt['extra']['byte_stride_hypotheses']['omitted'] == 0
        assert code in evaluated
        assert not result.exact

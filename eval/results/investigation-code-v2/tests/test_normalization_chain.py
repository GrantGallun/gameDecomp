import re
import pytest
from solver import modelrepair, stack_buffers, workspace


@pytest.mark.parametrize('stages,expected', [(2,2), (6,6), (99,8)])
def test_zero_model_repairs_compose_with_parent_lineage_and_bound(monkeypatch, tmp_path, stages, expected):
    root = workspace.Attempt(True, 10, False, '', '', '', 1, frontend={'passed': True})
    seen = []
    def candidates(source, *args):
        n = int(re.search(r'step(\d+)', source)[1])
        if n >= stages:
            return [], {}
        return [('stack-buffer-test', source.replace('step'+str(n), 'step'+str(n+1)))], {}
    def score(*args, **kwargs):
        seen.append(kwargs)
        return workspace.Attempt(True, 10+len(seen), False, '', '', '', 1+len(seen), frontend={'passed': True})
    monkeypatch.setattr(stack_buffers, 'candidates', candidates)
    monkeypatch.setattr(stack_buffers, 'byte_subfields', lambda *args: ([], {}))
    monkeypatch.setattr(workspace, 'score', score)
    monkeypatch.setattr(workspace, 'target_asm', lambda *args: 'f:\njr ra\nnop')
    r = modelrepair.search(tmp_path, 'f', 'void f(void) { step0(); }', tmp_path,
        model='test', endpoint='none', base_attempt=root, resilient=True, max_calls=0, beam_width=1)
    assert r.calls_attempted == 0 and r.normalization_candidates == expected
    # The preexisting diverse beam may prefer fewer repeated kinds; the byte
    # champion must still retain the best verified candidate from the chain.
    assert 'step'+str(expected) in r.best_byte.source
    assert [s['parent_attempt_id'] for s in seen] == list(range(1, expected+1))
    assert any('round limit' in line for line in r.log) == (stages > expected)


def test_cycle_is_not_recompiled(monkeypatch, tmp_path):
    root = workspace.Attempt(True, 10, False, '', '', '', 1, frontend={'passed': True})
    def candidates(source, *args):
        return [('stack-buffer-test', source.replace('first', 'second') if 'first' in source else source.replace('second', 'first'))], {}
    seen = []
    def score(*args, **kwargs):
        seen.append(kwargs)
        return workspace.Attempt(True, 20, False, '', '', '', 2, frontend={'passed': True})
    monkeypatch.setattr(stack_buffers, 'candidates', candidates)
    monkeypatch.setattr(stack_buffers, 'byte_subfields', lambda *args: ([], {}))
    monkeypatch.setattr(workspace, 'score', score)
    monkeypatch.setattr(workspace, 'target_asm', lambda *args: 'f:\njr ra\nnop')
    r = modelrepair.search(tmp_path, 'f', 'void f(void) { first(); }', tmp_path,
        model='test', endpoint='none', base_attempt=root, resilient=True, max_calls=0, beam_width=1)
    assert len(seen) == 1 and r.normalization_candidates == 1

import hashlib
from types import SimpleNamespace

from eval import semantic_lane


def state(compiled=True, frontend=True, source='child'):
    return SimpleNamespace(source=source, attempt=SimpleNamespace(
        compiled=compiled, frontend={'passed': frontend}))


def test_failed_root_does_not_disable_later_child(monkeypatch, tmp_path):
    calls = []
    class FakePanel:
        report = {'panel_sha256': 'fixed'}
        def __init__(self, *args):
            calls.append(args)
        def __call__(self, candidate):
            return {'panel_sha256': 'fixed', 'source': candidate.source}
    monkeypatch.setattr(semantic_lane, 'Panel', FakePanel)
    panel = semantic_lane.DeferredPanel(tmp_path, tmp_path, 'f')
    assert panel(state(False)) is None
    assert panel(state(frontend=False)) is None
    assert calls == []
    assert panel.report['status'] == 'not_initialized'
    assert panel(state())['source'] == 'child'
    assert panel(state(source='next'))['panel_sha256'] == 'fixed'
    assert len(calls) == 1
    assert panel.report == FakePanel.report


def test_unavailable_panel_retries_when_target_artifacts_appear(monkeypatch, tmp_path):
    calls = []
    class FakePanel:
        report = {'panel_sha256': 'fixed'}
        def __init__(self, *args):
            calls.append(args)
            (tmp_path / 'target.o').read_bytes()
        def __call__(self, candidate):
            return self.report
    monkeypatch.setattr(semantic_lane, 'Panel', FakePanel)
    panel = semantic_lane.DeferredPanel(tmp_path, tmp_path, 'f')
    report = panel(state())
    assert report['status'] == 'unavailable'
    assert report['authoritative'] is False
    assert report['source_sha256'] == hashlib.sha256(b'child').hexdigest()
    assert panel(state(source='next'))['status'] == 'unavailable'
    assert len(calls) == 1
    (tmp_path / 'target.o').write_bytes(b'target')
    assert panel(state())['panel_sha256'] == 'fixed'
    assert len(calls) == 2
    (tmp_path / 'target.o').write_bytes(b'changed')
    assert panel(state())['panel_sha256'] == 'fixed'
    assert len(calls) == 2


def test_failed_abi_probe_retries_with_new_header_context(monkeypatch,tmp_path):
    calls=[]
    class FakePanel:
        def __init__(self,*args):
            calls.append(args[-1])
            raise ValueError('missing active declaration')
    monkeypatch.setattr(semantic_lane,'Panel',FakePanel)
    panel=semantic_lane.DeferredPanel(tmp_path,tmp_path,'f')
    assert panel(state(source='#include "a.h"\nold'))['status']=='unavailable'
    panel(state(source='#include "a.h"\nnew'))
    assert len(calls)==1
    panel(state(source='#include "b.h"\nnew'))
    assert len(calls)==2

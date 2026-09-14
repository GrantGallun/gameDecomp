import copy
import json

import pytest

from eval import campaign_data as c
from solver import repair_queue


def fixture(tmp_path):
    repo, run = tmp_path/'repo', tmp_path/'run'
    paths = {
        repo/'asm/data.rodata.s': '.section .rodata, "a"\ndlabel constant\n/* 0 80001000 00000007 */ .word 7\nenddlabel constant\n',
        repo/'symbol_addrs.txt': 'constant = 0x80001000;\n',
        repo/'nonmatchings/f/target.s': 'glabel f\nlui $t0,%hi(constant)\nlw $v0,%lo(constant)($t0)\njr $ra\nnop\n',
        repo/'snowboardkids.z64': bytes.fromhex('00000007'),
    }
    pins = {}
    for path, text in paths.items():
        path.parent.mkdir(parents=True,exist_ok=True)
        raw = text.encode() if isinstance(text,str) else text
        path.write_bytes(raw)
        pins[str(path)] = c.sha(raw)
    node = {'status':'pending','source_sha256':'source','residual':{'compiled':True},'jobs':[]}
    return {'pins':pins,'nodes':{'f':node,'exact':{'status':'object_exact'}}}, repo, run


def test_controller_binds_once_and_workers_only_receive_their_packet(tmp_path, monkeypatch):
    state, repo, run = fixture(tmp_path)
    original = copy.deepcopy(state['nodes']['exact'])
    before = repair_queue.evidence_key(state['nodes']['f'])
    bundle, changed = c.prepare(state,repo,run)
    assert changed == ['f'] and bundle['summary']['rom_verified_bytes'] == 4
    assert bundle['summary']['typed_c_verified_bytes'] == 0
    assert state['nodes']['exact'] == original
    after = repair_queue.evidence_key(state['nodes']['f'])
    assert after != before
    monkeypatch.setattr(c.binary_data,'build',lambda *a,**kw: pytest.fail('must reuse verified cache'))
    again, changed = c.prepare(state,repo,run)
    assert again == bundle and changed == []
    assert repair_queue.evidence_key(state['nodes']['f']) == after
    config = {'model':'unchanged'}
    dispatched = c.worker_context(config,bundle,'f')
    assert set(dispatched['binary_data_context']) == {'f'}
    assert set(dispatched['binary_data_memory']) == {'f'}
    assert c.worker_context(config,bundle,'other') == config
    assert len(c.prompt(dispatched,'f')) < 4000
    assert config == {'model':'unchanged'}


def test_cache_tampering_and_changed_pinned_input_fail_closed(tmp_path):
    state, repo, run = fixture(tmp_path)
    c.prepare(state,repo,run)
    cache = run/state['binary_data_catalog']['path']
    cache.write_text('{}')
    with pytest.raises(ValueError,match='checksum'):
        c.prepare(state,repo,run)
    state.pop('binary_data_catalog')
    (repo/'symbol_addrs.txt').write_text('constant = 0x80002000;')
    with pytest.raises(ValueError,match='input changed'):
        c.prepare(state,repo,run)


def test_new_evidence_rebinds_at_drained_boundary_only(tmp_path):
    state, repo, run = fixture(tmp_path)
    state['fast_inflight'] = ['work']
    assert c.prepare(state,repo,run) == (None,[])
    state['fast_inflight'] = []
    old, _ = c.prepare(state,repo,run)
    state['fast_inflight'] = ['work']
    path = repo/'symbol_addrs.txt'
    path.write_text('constant = 0x80001000;\nother = 0x80002000;\n')
    state['pins'][str(path)] = c.sha(path.read_bytes())
    assert c.prepare(state,repo,run) == (old,[])
    state['fast_inflight'] = []
    new, changed = c.prepare(state,repo,run)
    assert new['input_sha256'] != old['input_sha256']
    assert changed == ['f']


def test_missing_previously_bound_inputs_are_rejected(tmp_path):
    state, repo, run = fixture(tmp_path)
    c.prepare(state,repo,run)
    state['pins'].pop(str(repo/'snowboardkids.z64'))
    with pytest.raises(ValueError,match='unavailable'):
        c.prepare(state,repo,run)


def test_deferred_panel_forwards_bound_memory_context(monkeypatch,tmp_path):
    from eval import semantic_lane
    from types import SimpleNamespace
    seen = []
    class Panel:
        def __init__(self,*args,**kwargs):
            seen.append(kwargs['memory_context'])
        def __call__(self,state):
            return {'status':'passed'}
    monkeypatch.setattr(semantic_lane,'Panel',Panel)
    context = {'sha256':'bound'}
    panel = semantic_lane.DeferredPanel(tmp_path,tmp_path,'f',memory_context=context)
    state = SimpleNamespace(source='child',attempt=SimpleNamespace(compiled=True,frontend={'passed':True}))
    assert panel(state)['status'] == 'passed' and seen == [context]

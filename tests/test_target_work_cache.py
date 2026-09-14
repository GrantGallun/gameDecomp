import copy
import json
import os
from pathlib import Path

import pytest

from eval import target_work_cache as cache
from solver import mips_differential as d, callee_execution as callees


ASM = 'lw v0,0(a0)\nbeq v0,zero,done\nnop\naddiu v0,v0,1\ndone:\njr ra\nnop'
SEEDS = (d.TestCase('seed', 1),)


def test_full_result_roundtrip_preserves_traces_debt_and_nonstring_keys(tmp_path):
    for result in (
        d.explore_coverage(ASM, SEEDS, max_cases=8, max_total_steps=60),
        d.build_semantic_stress_panel(ASM, SEEDS, max_cases=4, max_trials=6),
        d.explore_coverage('loop:\nb loop\nnop', SEEDS, max_steps=5, max_total_steps=11),
    ):
        cache.save(tmp_path, result)
        restored = cache.read(tmp_path)
        assert restored == result
        assert restored.to_dict() == result.to_dict()
        assert restored is not result
        assert all(a.trace == b.trace for a,b in zip(restored.runs,result.runs))
    value = {(1, 'symbol'): (b'\x00\xff', [1, 'tuple'])}
    assert cache.decode(json.loads(json.dumps(cache.encode(value)))) == value
    ordered = {'b':1, 'a':2}
    assert list(cache.decode(cache.encode(ordered))) == ['b','a']
    assert cache.encode(ordered) != cache.encode({'a':2,'b':1})


def test_complete_environment_and_budget_bind_identity():
    env = callees.Environment(leaves={'helper':callees.Leaf('jr ra\nnop', 'ROM receipt')})
    options = {'callee_environment':env, 'max_cases':4}
    original = cache.identity(d.explore_coverage, (ASM, SEEDS), options)
    for change in ({'max_cases':5}, {'max_steps':12}, {'call_arities':{'helper':2}},
                   {'return_registers':('v0',)}, {'pointer_entry_registers':('a2',)}):
        assert cache.identity(d.explore_coverage, (ASM, SEEDS), {**options, **change}) != original
    changed = copy.deepcopy(env)
    changed.leaves['helper'] = callees.Leaf('jr ra\naddiu v0,zero,1', 'ROM receipt')
    assert cache.identity(d.explore_coverage, (ASM, SEEDS), {**options,'callee_environment':changed}) != original
    assert cache.identity(d.explore_coverage, (ASM+'\n# MIPS_DIFF_EXTENT data 32',SEEDS), options) != original
    assert cache.identity(d.explore_coverage, (ASM,(d.TestCase('other',2),)), options) != original
    with pytest.raises(TypeError):
        cache.identity(d.explore_coverage, (ASM,SEEDS), {'callee_environment':object()})


def test_corrupt_or_executable_cache_rejected(tmp_path):
    result = d.explore_coverage(ASM, SEEDS, max_cases=2)
    cache.save(tmp_path,result)
    path=tmp_path/'target.bin'
    content=path.read_bytes()
    path.write_bytes(b'x'+content[1:])
    with pytest.raises(ValueError, match='checksum'):
        cache.read(tmp_path)
    with pytest.raises(ValueError):
        cache.decode(['record','os.system',{'command':'bad'}])


def test_large_trace_bypasses_storage_and_reader_is_bounded(tmp_path,monkeypatch):
    result=d.explore_coverage(ASM,SEEDS,max_cases=2)
    monkeypatch.setattr(cache,'MAX_TRACE_ENTRIES',0)
    with pytest.raises(TypeError,match='trace admission'):
        cache.save(tmp_path,result)
    assert not (tmp_path/'target.bin').exists()
    monkeypatch.setattr(cache,'MAX_TRACE_ENTRIES',25000)
    cache.save(tmp_path,result)
    monkeypatch.setattr(cache,'MAX_PAYLOAD_BYTES',8)
    with pytest.raises(ValueError,match='limit'):
        cache.read(tmp_path)


@pytest.mark.skipif(os.name=='nt', reason='fast runtime requires flock')
def test_target_cache_fresh_wrappers_and_pin_changes(tmp_path):
    from eval import fast_runtime
    from solver import workspace
    originals=(d.explore_coverage,d.build_semantic_stress_panel,workspace.sh)
    previous_pin=getattr(workspace,'_verified_build_cache_pin',None)
    metrics=fast_runtime.install(tmp_path/'cache','pin',tmp_path/'model.lock')
    try:
        cold=d.explore_coverage(ASM,SEEDS,max_cases=8,max_total_steps=80)
        warm=d.explore_coverage(ASM,SEEDS,max_cases=8,max_total_steps=80)
        assert cold == warm and metrics['target_work_hits']==1
        # Mutating returned report data cannot poison the persistent entry.
        cold.trial_status_counts['fake']=1
        assert 'fake' not in d.explore_coverage(ASM,SEEDS,max_cases=8,max_total_steps=80).trial_status_counts
        next_metrics=fast_runtime.install(tmp_path/'cache','different-pin',tmp_path/'model.lock')
        try:
            d.explore_coverage(ASM,SEEDS,max_cases=8,max_total_steps=80)
            assert next_metrics['target_work_misses']==1 and next_metrics['target_work_hits']==0
            assert metrics['target_work_misses']==1
            assert workspace._verified_build_cache_pin=='different-pin'
        finally:
            next_metrics.close()
    finally:
        metrics.close()
    assert (d.explore_coverage,d.build_semantic_stress_panel,workspace.sh)==originals
    assert getattr(workspace,'_verified_build_cache_pin',None)==previous_pin

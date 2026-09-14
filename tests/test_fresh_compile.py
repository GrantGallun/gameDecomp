"""Same-job artifact addressing retains ordinary verifier and lineage work."""
import hashlib
import os
from pathlib import Path

import pytest

from solver import fresh_compile, workspace


@pytest.fixture(autouse=True)
def scoped_runtime_wrappers():
    from eval import fast_runtime
    if fast_runtime._active is not None:
        fast_runtime._active.close()
    yield
    if fast_runtime._active is not None:
        fast_runtime._active.close()


def test_names_keep_scoring_and_parent_lineage_and_decline_changed_inputs(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(workspace, '_verified_build_cache_pin', 'pin1')
    def score(ws, repo, name, source, **kwargs):
        calls.append((name, source, kwargs))
        return workspace.Attempt(True, 80, False, '', '', '', len(calls))
    monkeypatch.setattr(workspace, 'score', score)
    names = fresh_compile.Names(tmp_path, tmp_path)
    source = 'const char *f(void) { return __FILE__; }'
    first, one = names.score('root', source, parent_attempt_id=5)
    second, two = names.score('retained', source, parent_attempt_id=9, extra={'hypothesis': 'kept'})
    assert first == second == 'root'  # actual filename stays bound, even for __FILE__
    assert one.receipt_id != two.receipt_id and len(calls) == 2
    assert calls[1][2]['parent_attempt_id'] == 9
    assert calls[1][2]['extra']['hypothesis'] == 'kept'
    assert calls[1][2]['extra']['same_job_build_reuse']['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
    assert names.score('different', source + '\n')[0] == 'different'
    monkeypatch.setattr(workspace, '_verified_build_cache_pin', 'pin2')
    assert names.score('amended', source)[0] == 'amended'
    assert fresh_compile.Names(tmp_path, tmp_path).prior(source) is None


@pytest.mark.parametrize('enabled,compiled', [(False, True), (True, False)])
def test_unverified_runtime_and_failed_builds_keep_distinct_names(tmp_path, monkeypatch, enabled, compiled):
    monkeypatch.setattr(workspace, '_verified_build_cache_pin', 'pin' if enabled else None)
    monkeypatch.setattr(workspace, 'score', lambda *a, **k: workspace.Attempt(compiled, 0, False, '', '', ''))
    names = fresh_compile.Names(tmp_path, tmp_path)
    assert names.score('one', 'source')[0] == 'one'
    assert names.score('two', 'source')[0] == 'two'


def test_deterministic_baseline_reuses_address_but_runs_score(tmp_path, monkeypatch):
    from solver import repair
    seen = []
    def score(ws, repo, name, source, **kwargs):
        seen.append((name, source))
        return workspace.Attempt(True, 80, False, '', '', '')
    monkeypatch.setattr(workspace, 'score', score)
    att, source, _ = repair.search(tmp_path, 'f', 'root source', tmp_path,
        baseline_name='fresh_root', parent_attempt_id=7, max_pairs=0)
    assert seen == [('fresh_root', 'root source')]
    assert att.receipt_id == 7 and source == 'root source'


@pytest.mark.skipif(os.name == 'nt', reason='live build cache uses flock under WSL')
def test_name_reuse_hits_existing_cache_and_keeps_frontend_and_attribution(tmp_path, monkeypatch):
    from eval import fast_runtime, semantic_lane
    from solver import compiler_recipe, frontend_check, llm, source_attribution, type_constraints
    ws = tmp_path / 'ws'
    ws.mkdir()
    (tmp_path / 'Makefile').write_text('CC_CHECK = clang\n')
    script = ws / 'build.sh'
    script.write_text('exact test build script')
    calls = {'build': 0, 'frontend': 0, 'attribution': 0}
    def shell(cmd, cwd=None, timeout=300):
        calls['build'] += 1
        name = cmd.split()[-1][:-2]
        (cwd / (name + '.o')).write_bytes(b'compiled object')
        (cwd / (name + '_diff')).write_text('unchanged diff')
        return 0, 'Score: 80%\n'
    def frontend(*args):
        calls['frontend'] += 1
        return {'passed': True}
    def collect(ws, name, code, compile_source, diff):
        calls['attribution'] += 1
        return {'source_sha256': hashlib.sha256(code.encode()).hexdigest()}
    monkeypatch.setattr(workspace, 'sh', shell)
    monkeypatch.setattr(compiler_recipe, 'prepare', lambda *a: (script, {'target': 'target'}))
    monkeypatch.setattr(source_attribution, 'prepare', lambda ws, name, script: script)
    monkeypatch.setattr(source_attribution, 'collect', collect)
    monkeypatch.setattr(frontend_check, 'check', frontend)
    # Restore all process-global wrappers when the test finishes.
    for owner, name in ((llm, 'generate'), (type_constraints, 'measure'), (semantic_lane.Panel, '__call__')):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    monkeypatch.setattr(workspace, '_verified_build_cache_pin', 'pin')
    metrics = fast_runtime.install(tmp_path / 'cache', 'pin', tmp_path / 'model.lock')
    names = fresh_compile.Names(tmp_path, ws)
    name, first = names.score('root', 'int f(void){return 1;}')
    (ws / (name + '.o')).write_bytes(b'corrupted transient output')
    name, second = names.score('retained', 'int f(void){return 1;}')
    assert calls == {'build': 1, 'frontend': 2, 'attribution': 2}
    assert metrics['compile_hits'] == 1
    assert (ws / (name + '.o')).read_bytes() == b'compiled object'
    assert first.frontend == second.frontend == {'passed': True}
    # Script/target inputs are still part of the existing cache key.
    script.write_text('changed recipe')
    names.score('third', 'int f(void){return 1;}')
    assert calls['build'] == 2 and calls['frontend'] == 3
    (ws / 'target.s').write_text('changed target')
    names.score('fourth', 'int f(void){return 1;}')
    assert calls['build'] == 3 and calls['frontend'] == 4

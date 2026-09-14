import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval import campaign_runtime as runtime
from solver import project64_capture


@pytest.fixture
def harness(tmp_path, monkeypatch):
    source = tmp_path / 'selected.c'
    source.write_text('int f(void) {return 0;}')
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'snowboardkids.z64').write_bytes(b'ROM')
    plan = {'architecture': 'mips-o32-be', 'byte_order': 'big', 'function': 'f',
            'entry': 0x80000400, 'code_size': 4, 'rom_offset': 0,
            'rom_sha256': runtime.sha(repo/'snowboardkids.z64'), 'registers': project64_capture.register_map(),
            'ram': [{'name': 'data', 'address': 0x80001000, 'size': 4, 'kind': 'persistent'}]}
    manifest = {'schema_version': 1, 'windows_python': 'C:/Python314/python.exe',
                'portable_dir': 'C:/portable', 'refresh_seconds': 86400,
                'plans': [{'id': 'first', 'plan': plan, 'selector': 'first'},
                          {'id': 'nonzero', 'plan': plan, 'selector': 'a0_nonzero'}]}
    path = tmp_path / 'plans.json'
    path.write_text(json.dumps(manifest))
    node = {'status': 'pending', 'source': str(source), 'source_sha256': runtime.sha(source), 'attempt_id': 7,
            'verification': {}, 'semantic_validation': {'status': 'observed_pass', 'source_sha256': runtime.sha(source),
                                                        'panel_sha256': 'old', 'counts': {'passed': 1}}}
    state = {'nodes': {'f': node}, 'pins': {},
             'config': {'runtime_capture_plan_sha256': runtime.sha(path), 'db': str(tmp_path/'unused.sqlite')}}
    calls = []
    def launch(manifest, item, **kw):
        calls.append(item['id'])
        return {'sha256': item['id'], 'plan': item['plan']}
    def verify(record, rom):
        if record.get('sha256') == 'tampered':
            raise ValueError('capture checksum mismatch')
    def evaluate(**kw):
        return ({'comparison': {'status': 'passed'}},
                {'source_sha256': kw['node']['source_sha256'], 'status': 'observed_pass',
                 'panel_sha256': 'captured-' + '-'.join(c['sha256'] for c in kw['captures']), 'counts': {'passed': 3}})
    monkeypatch.setattr(runtime, 'launch', launch)
    monkeypatch.setattr(runtime, 'compile_candidate', lambda **kw: None)
    monkeypatch.setattr(runtime, 'evaluate', evaluate)
    monkeypatch.setattr(runtime.runtime_capture, 'verify', verify)
    def pins(pins):
        if any(runtime.sha(path) != digest for path, digest in pins.items()):
            raise ValueError('build input changed')
    monkeypatch.setattr(runtime.campaign.frozen_wavefront, 'verify_files', pins)
    args = dict(repo=repo, artifacts=tmp_path/'campaign-artifacts', plan_path=path, checkpoint=12)
    return state, args, calls, evaluate


def test_new_captures_join_current_candidate_panel_and_are_not_repeated(harness):
    state, args, calls, _ = harness
    before = copy.deepcopy(state['nodes']['f']['semantic_validation'])
    saved = []
    assert runtime.sweep(state, **args, on_progress=lambda **kw: saved.append(kw)) == ['f']
    assert calls == ['first', 'nonzero']
    assert state['nodes']['f']['status'] == 'pending'
    assert state['nodes']['f']['semantic_validation']['panel_sha256'] != before['panel_sha256']
    assert len(state['config']['runtime_captures']['f']) == 2
    assert len([kw for kw in saved if kw.get('changed') == ['f']]) == 2
    latest = state['runtime_capture']['latest']
    assert latest['checkpoint'] == 12 and latest['counts'] == {'passed': 1, 'failed': 0, 'inconclusive': 0}
    assert any(a['path'].endswith('replay.json') for a in latest['artifacts'])
    assert runtime.sweep(state, **args) == []
    assert calls == ['first', 'nonzero']


def test_new_source_recaptures_without_accumulating_duplicate_inputs(harness):
    state, args, calls, _ = harness
    runtime.sweep(state, **args)
    node = state['nodes']['f']
    Path(node['source']).write_text('int f(void) {return 1;}')
    node['source_sha256'] = runtime.sha(node['source'])
    runtime.sweep(state, **args)
    assert calls == ['first', 'nonzero', 'first', 'nonzero']
    assert len(state['config']['runtime_captures']['f']) == 2


@pytest.mark.parametrize('status', ['object_exact', 'integrated', 'function_exact_pending_integration'])
def test_exact_ratchet_preserved_even_when_capture_fails(harness, monkeypatch, status):
    state, args, _, _ = harness
    node = state['nodes']['f']
    node['status'] = status
    before = copy.deepcopy(node['semantic_validation'])
    monkeypatch.setattr(runtime, 'evaluate', lambda **kw: ({'comparison': {'status': 'failed'}}, None))
    runtime.sweep(state, **args)
    assert node['status'] == status and node['semantic_validation'] == before
    assert node['runtime_capture_validation']['status'] == 'failed'
    assert len(state['config']['runtime_captures']['f']) == 2


@pytest.mark.parametrize('fault', ['source', 'capture', 'compile', 'replay', 'combined_identity', 'pins'])
def test_unavailable_or_stale_evidence_never_imported(harness, monkeypatch, fault):
    state, args, _, evaluate = harness
    before = copy.deepcopy(state['nodes']['f']['semantic_validation'])
    if fault == 'pins':
        header = args['repo']/'header.h'
        header.write_text('old')
        state['pins'][str(header)] = runtime.sha(header)
    def bad_launch(manifest, item, **kw):
        if fault == 'source':
            Path(state['nodes']['f']['source']).write_text('changed externally')
        if fault == 'pins':
            (args['repo']/'header.h').write_text('changed')
        return {'sha256': 'tampered' if fault == 'capture' else 'ok', 'plan': item['plan']}
    monkeypatch.setattr(runtime, 'launch', bad_launch)
    if fault in {'compile', 'replay'}:
        def unavailable(**kw):
            raise ValueError('unsupported ' + fault)
        monkeypatch.setattr(runtime, 'compile_candidate' if fault == 'compile' else 'evaluate', unavailable)
    elif fault == 'combined_identity':
        monkeypatch.setattr(runtime, 'evaluate', lambda **kw:
                            ({'comparison': {'status': 'passed'}}, {'source_sha256': 'wrong'}))
    runtime.sweep(state, **args)
    assert state['nodes']['f']['semantic_validation'] == before
    assert not state['config'].get('runtime_captures')
    assert state['runtime_capture']['latest']['status'] == 'unavailable'


def test_explicit_manifest_binding_and_drained_boundary(harness):
    state, args, calls, _ = harness
    state['fast_inflight'] = [{}]
    with pytest.raises(ValueError, match='drained'):
        runtime.sweep(state, **args)
    state['fast_inflight'] = []
    args['plan_path'].write_text('{}')
    with pytest.raises(ValueError, match='amendment'):
        runtime.sweep(state, **args)
    assert calls == []


def test_cadence_refreshes_and_active_inputs_remain_bounded(harness, monkeypatch):
    state, args, calls, _ = harness
    runtime.sweep(state, **args)
    future = runtime.time.time() + 86401
    monkeypatch.setattr(runtime.time, 'time', lambda: future)
    runtime.sweep(state, **args)
    assert len(calls) == 4 and len(state['config']['runtime_captures']['f']) == 2


def test_amended_plan_removes_old_managed_capture(harness):
    state, args, calls, _ = harness
    runtime.sweep(state, **args)
    manifest = json.loads(args['plan_path'].read_bytes())
    manifest['plans'] = manifest['plans'][1:]
    args['plan_path'].write_text(json.dumps(manifest))
    state['config']['runtime_capture_plan_sha256'] = runtime.sha(args['plan_path'])
    runtime.sweep(state, **args)
    assert list(state['runtime_capture']['active']) == ['nonzero']
    assert [c['sha256'] for c in state['config']['runtime_captures']['f']] == ['nonzero']


def test_shared_input_owned_by_other_plan_survives_refresh(harness, monkeypatch):
    state, args, calls, _ = harness
    version = [0]
    monkeypatch.setattr(runtime, 'launch', lambda manifest, item, **kw:
                        {'sha256': 'new-first' if version[0] and item['id'] == 'first' else 'same', 'plan': item['plan']})
    runtime.sweep(state, **args)
    assert len(state['config']['runtime_captures']['f']) == 1
    version[0] = 1
    state['nodes']['f']['attempt_id'] += 1
    runtime.sweep(state, **args)
    assert {c['sha256'] for c in state['config']['runtime_captures']['f']} == {'new-first', 'same'}


def test_windows_interop_exec_format_fallback_keeps_preserved_argv(harness, monkeypatch):
    import errno
    from types import SimpleNamespace
    state, args, _, _ = harness
    manifest = json.loads(args['plan_path'].read_bytes())
    folder = args['artifacts']/'launch-test'
    folder.mkdir(parents=True)
    commands = []
    def run(command, **kw):
        commands.append(command)
        if len(commands) == 1:
            raise OSError(errno.ENOEXEC, 'Exec format error')
        output = folder/'emulator'
        output.mkdir()
        raw = output/'project64-entry-raw.json'
        raw.write_text('{}')
        (output/'receipt.json').write_text(json.dumps({'status': 'captured', 'raw_path': str(raw)}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    monkeypatch.setattr(runtime, 'linux_path', lambda p: Path(p))
    monkeypatch.setattr(runtime, 'windows_path', lambda p: str(p))
    original = Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda p: True if p == Path('/init') else original(p))
    monkeypatch.setattr(runtime.project64_capture, 'import_export', lambda raw, plan, rom: {'sha256': 'ok', 'plan': plan})
    # Fixture launch is mocked; call the original source-defined helper.
    import importlib.util
    spec = importlib.util.spec_from_file_location('runtime_launch_test', runtime.__file__)
    actual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual)
    monkeypatch.setattr(actual, 'linux_path', runtime.linux_path)
    monkeypatch.setattr(actual, 'windows_path', runtime.windows_path)
    record = actual.launch(manifest, manifest['plans'][0], rom=args['repo']/'snowboardkids.z64', folder=folder)
    assert record['sha256'] == 'ok'
    assert commands[1] == ['/init', commands[0][0], *commands[0]]


def test_evaluate_uses_compiled_candidate_not_target(harness, monkeypatch):
    from types import SimpleNamespace
    state, args, _, _ = harness
    observed = []
    def replay(record, rom, target, candidate, **kw):
        observed.append((target, candidate))
        return {'comparison': {'status': 'failed'}}
    monkeypatch.setattr(runtime.runtime_capture, 'replay', replay)
    state['nodes']['f']['status'] = 'object_exact'
    # Retrieve the original helper, bypassing this fixture's orchestration mock.
    import importlib.util
    spec = importlib.util.spec_from_file_location('runtime_evaluate_test', runtime.__file__)
    actual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual)
    result, combined = actual.evaluate(compiled=(args['repo'], args['repo'], SimpleNamespace(), 'target assembly', 'compiled candidate assembly'),
                                       record={'plan': {'function': 'f'}}, captures=[], repo=args['repo'],
                                       rom=args['repo']/'snowboardkids.z64', node=state['nodes']['f'], config={})
    assert observed == [('target assembly', 'compiled candidate assembly')]
    assert result['comparison']['status'] == 'failed' and combined is None

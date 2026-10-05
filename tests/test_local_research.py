import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from eval.local_research import Budget, Halt, Research, validate_hypothesis, verdict
from eval.research_tools import local_endpoint, validate_model


def hypothesis():
    return dict(title='Narrow the mask', rationale='Observe immediate AND instructions',
                before='u32 syn_probe(u32 x) { return x + {{K}}; }',
                after='u32 syn_probe(u32 x) { return x & {{K}}; }',
                metric='andi_ops', relation='greater')


@pytest.mark.parametrize('url', ['https://ollama.com', 'http://example.org:11434',
    'http://127.0.0.1:11434/api', 'http://me:secret@127.0.0.1:11434',
    'http://127.0.0.1:11434?x=1', 'http://127.0.0.1:80', 'http://172.28.32.2:11434'])
def test_paid_or_unexpected_destinations_are_rejected(url):
    with pytest.raises(ValueError):
        local_endpoint(url, gateway='172.28.32.1')


def test_only_loopback_and_actual_host_gateway_are_allowed():
    assert local_endpoint('http://127.0.0.1:11434', gateway=None).endswith(':11434')
    assert local_endpoint('http://172.28.32.1:11434', gateway='172.28.32.1').endswith(':11434')


def test_cloud_alias_or_missing_weights_cannot_use_local_host_as_a_proxy():
    tag = dict(name='local:latest', digest='a' * 64, size=1000,
               details={'format': 'gguf', 'parameter_size': '20B'})
    show = dict(details={'format': 'gguf'}, model_info={'general.architecture': 'gptoss'})
    assert validate_model('local:latest', {'models': [tag]}, show)['digest'] == 'a' * 64
    for name, tags, details in [
        ('absent', {'models': [tag]}, show),
        ('local:latest', {'models': [tag]}, {**show, 'remote_host': 'https://ollama.com'}),
        ('local:latest', {'models': [{**tag, 'remote_model': 'paid'}]}, show),
        ('local-cloud', {'models': [{**tag, 'name': 'local-cloud'}]}, show),
        ('local:latest', {'models': [{**tag, 'size': 0}]}, show),
    ]:
        with pytest.raises(ValueError):
            validate_model(name, tags, details)


def test_generated_source_cannot_include_files_or_assembly():
    assert validate_hypothesis(hypothesis())['metric'] == 'andi_ops'
    for source in ['#include "/etc/passwd"\nu32 syn_probe(){return {{K}};}',
                   'asm("x"); u32 syn_probe(){return {{K}};}',
                   'u32 syn_probe(){return sizeof("{{K}}");}',
                   '??=include <common.h>\nu32 syn_probe(){return {{K}};}',
                   'u32 other(){return {{K}};}']:
        with pytest.raises(ValueError):
            validate_hypothesis({**hypothesis(), 'after': source})


def test_limits_are_reserved_before_work_and_cannot_be_replenished(tmp_path):
    b = Budget(minutes=1, max_calls=1, max_compiles=2, stop=tmp_path/'stop', run_id='r')
    b.reserve('calls')
    with pytest.raises(Halt, match='call budget'):
        b.reserve('calls')
    b.reserve('compiles')
    (tmp_path/'stop').write_text(json.dumps({'run_id': 'old'}))
    b.check()
    (tmp_path/'stop').write_text(json.dumps({'run_id': 'r'}))
    with pytest.raises(Halt, match='Stopped'):
        b.check()


def test_deadline_and_stop_interrupt_active_child(tmp_path):
    b = Budget(minutes=1, max_calls=1, max_compiles=1, stop=tmp_path/'stop', run_id='r')
    b.deadline = time.monotonic() + 0.25
    start = time.monotonic()
    with pytest.raises(Halt, match='time budget'):
        b.execute([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=30)
    assert time.monotonic() - start < 3


def test_confirmation_requires_all_six_successful_compiles_and_the_prediction():
    rows = [dict(phase=p, k=k, before={'compiled': True, 'features': {'andi_ops': 0}},
                 after={'compiled': True, 'features': {'andi_ops': 1}})
            for p, k in [('discovery', 3), ('confirmation', 5), ('confirmation', 9)]]
    assert verdict(hypothesis(), rows) == 'synthetic_confirmed'
    assert verdict(hypothesis(), rows[:1]) == 'incomplete'
    rows[-1]['after']['features']['andi_ops'] = 0
    assert verdict(hypothesis(), rows) == 'counterexample'
    rows[-1]['after'] = {'compiled': False}
    assert verdict(hypothesis(), rows) == 'compile_failed'


def test_failed_calls_are_logged_and_no_extra_request_is_made(tmp_path):
    calls = []
    def tool(op, payload, **kw):
        if op == 'inspect':
            return {'endpoint': 'http://127.0.0.1:11434', 'digest': 'a'*64}
        if op == 'recipe':
            return {'command': ['cc'], 'target': 'fake', 'identity': 'test'}
        calls.append(op)
        raise RuntimeError('connection dropped')
    lab = Research(tmp_path/'lab', tmp_path/'view.json', tmp_path/'stop',
                   repo=tmp_path, max_calls=1, tool=tool)
    result = lab.run()
    assert calls == ['chat']
    assert result['calls'] == 1 and result['status'] == 'finished'
    events = [json.loads(x) for x in (lab.run_dir/'events.jsonl').read_text().splitlines()]
    assert any(e['event'] == 'call_started' for e in events)
    assert any(e['event'] == 'call_failed' and 'connection dropped' in e['error'] for e in events)


def test_research_uses_confirmation_and_remembers_counterexamples(tmp_path):
    prompts = []
    def tool(op, payload, **kw):
        if op == 'inspect':
            return {'endpoint': 'http://127.0.0.1:11434', 'digest': 'a'*64}
        if op == 'recipe':
            return {'command': ['cc'], 'target': 'fake', 'identity': 'test'}
        if op == 'chat':
            prompts.append(payload['messages'])
            return {'message': {'content': json.dumps(hypothesis())}, 'done': True}
        return {'compiled': True, 'features': {'andi_ops': 0}, 'asm': 'jr ra'}
    lab = Research(tmp_path/'lab', tmp_path/'view.json', tmp_path/'stop',
                   repo=tmp_path, max_calls=2, tool=tool)
    result = lab.run()
    assert result['compiles'] == 2       # discovery refutes it; duplicate proposal is skipped
    assert result['counterexamples'] == 1 and result['confirmed'] == 0
    assert 'counterexample' in json.dumps(prompts[1])
    notebook = [json.loads(x) for x in (lab.state/'notebook.jsonl').read_text().splitlines()]
    assert notebook[0]['recipe_id'] == 'test'
    ks = [r['k'] for r in notebook[0]['measurements']]
    assert ks == [3]
    assert notebook[0]['game_transfer'] == 'untested'


def test_successful_hypothesis_must_fire_on_two_independent_confirmation_values(tmp_path):
    def tool(op, payload, **kw):
        if op == 'inspect':
            return {'endpoint': 'http://127.0.0.1:11434', 'digest': 'a'*64}
        if op == 'recipe':
            return {'command': ['cc'], 'identity': 'test'}
        if op == 'chat':
            return {'message': {'content': json.dumps(hypothesis())}, 'done': True}
        return {'compiled': True, 'features': {'andi_ops': int('&' in payload['source'])}, 'asm': 'jr ra'}
    lab = Research(tmp_path/'lab', tmp_path/'view', tmp_path/'stop', repo=tmp_path, max_calls=1, tool=tool)
    result = lab.run()
    assert result['confirmed'] == 1 and result['compiles'] == 6
    rows = result['recent'][0]['measurements']
    assert len({r['k'] for r in rows}) == 3
    assert [r['phase'] for r in rows] == ['discovery', 'confirmation', 'confirmation']


def test_retrieval_is_bounded_and_does_not_load_assembly_into_prompts(tmp_path):
    state = tmp_path/'lab'
    state.mkdir()
    rows = [dict(id=str(i), pair_id=str(i), recipe_id='test', proposal=hypothesis(),
                 status='counterexample', measurements=[{'phase': 'discovery', 'k': 3,
                    'before': {'compiled': True, 'features': {'andi_ops': 0}, 'asm': 'BIGASM'*10000},
                    'after': {'compiled': True, 'features': {'andi_ops': 0}, 'asm': 'BIGASM'*10000}}])
            for i in range(7)]
    (state/'notebook.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    lab = Research(state, tmp_path/'view', tmp_path/'stop', repo=tmp_path)
    memory, seen = lab.memory('test')
    messages = lab.prompt(memory, 1)
    assert 'BIGASM' not in json.dumps(messages)
    assert len(json.dumps(messages)) < 24000
    assert seen == {str(i) for i in range(7)}


def test_stop_cancels_a_request_and_logs_the_interruption(tmp_path):
    b = Budget(minutes=1, max_calls=1, max_compiles=1, stop=tmp_path/'stop', run_id='r')
    timer = threading.Timer(0.2, lambda: (tmp_path/'stop').write_text('{"run_id":"r"}'))
    timer.start()
    try:
        with pytest.raises(Halt, match='Stopped by user'):
            b.execute([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=30)
    finally:
        timer.join()


@pytest.mark.skipif(os.name == 'nt', reason='The worker and compiler run in Linux')
def test_exited_tool_leader_does_not_leave_a_compiler_descendant(tmp_path, monkeypatch):
    killed = []
    actual_killpg = os.killpg
    def killpg(group, sig):
        killed.append(group)
        actual_killpg(group, sig)
    monkeypatch.setattr(os, 'killpg', killpg)
    code = ('import subprocess, sys; '
            'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],'
            'stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); '
            'print(p.pid, flush=True)')
    b = Budget(minutes=1, max_calls=1, max_compiles=1, stop=tmp_path/'stop', run_id='r')
    child = int(b.execute([sys.executable, '-c', code], timeout=5).strip())
    try:
        assert killed, 'Tool group must be cleaned even after its leader has exited'
    finally:
        try:
            os.kill(child, 9)
        except ProcessLookupError:
            pass

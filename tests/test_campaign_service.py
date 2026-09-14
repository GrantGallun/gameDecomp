import json
import sys

import pytest

from eval import campaign_service as service


def test_restart_command_changes_only_process_budget(tmp_path):
    command = ['python', '-m', 'eval.completion_campaign', '--max-work-items', '1000', '--model-calls', '3']
    (tmp_path / 'launch.json').write_text(json.dumps({'command': command}))
    assert service.command_for(tmp_path, 10) == command[:4] + ['10'] + command[5:] + ['--resume']


@pytest.mark.parametrize('code,status,count,paused,expected', [
    (0, 'paused_budget', 0, False, 'restart'),
    (1, 'paused_budget', 1, False, 'retry'),
    (1, 'paused_budget', 3, False, 'needs_repair'),
    (0, 'paused_inputs_changed', 0, False, 'needs_repair'),
    (0, 'complete', 0, False, 'finished'),
    (1, 'paused_budget', 1, True, 'paused'),
])
def test_retry_policy(code, status, count, paused, expected):
    assert service.retry_decision(code, status, count, paused) == expected


def test_metrics_use_nodes_not_stale_summary():
    result = service.checkpoint_health({'summary': {'object_exact_or_integrated': 1},
        'nodes': {'f': {'status': 'object_exact'}, 'g': {'status': 'object_exact'},
                  'h': {'status': 'pending', 'semantic_validation': {'status': 'observed_pass_with_execution_debt'}}}})
    assert result['states']['object_exact'] == 2
    assert result['semantic_functions'] == {'observed_pass_with_execution_debt': 1}


def test_worker_crash_restarts_from_durable_checkpoint(tmp_path, monkeypatch):
    (tmp_path / 'code').mkdir()
    worker = tmp_path / 'worker.py'
    worker.write_text("""import json,pathlib,sys
p=pathlib.Path(__file__).parent/'campaign.json'
s=json.loads(p.read_text());s['completed']+=1
s['status']='paused_budget' if s['completed']==1 else 'complete'
p.write_text(json.dumps(s))
sys.exit(1 if s['completed']==1 else 0)
""")
    service.save(tmp_path / 'launch.json', {'command': [sys.executable, str(worker), '--max-work-items', '1000']})
    service.save(tmp_path / 'campaign.json', {'completed': 0, 'status': 'paused_budget'})
    monkeypatch.setattr(service.time, 'sleep', lambda _: None)
    service.supervise(tmp_path, 10)
    assert service.read(tmp_path / 'campaign.json')['completed'] == 2
    assert service.read(tmp_path / 'checkpoint.previous.json')['completed'] == 1
    assert service.read(tmp_path / 'service.json')['status'] == 'finished'


def test_persistent_failure_stops_after_three_workers(tmp_path, monkeypatch):
    (tmp_path / 'code').mkdir()
    worker = tmp_path / 'worker.py'
    worker.write_text('import sys; sys.exit(1)')
    service.save(tmp_path / 'launch.json', {'command': [sys.executable, str(worker), '--max-work-items', '1000']})
    service.save(tmp_path / 'campaign.json', {'status': 'paused_budget'})
    monkeypatch.setattr(service.time, 'sleep', lambda _: None)
    service.supervise(tmp_path, 10)
    result = service.read(tmp_path / 'service.json')
    assert result['status'] == 'needs_repair'
    assert result['consecutive_failures'] == 3

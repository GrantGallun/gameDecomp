import json
import subprocess

import pytest

from eval import progress_app
from eval.progress_app import Control, recent_results, snapshot, wsl_path


def test_control_runs_only_the_services_own_pause_and_resume(tmp_path, monkeypatch):
    run = tmp_path / 'run'
    run.mkdir()
    control = Control(run, 'Ubuntu', '/venv/bin/python', tmp_path / 'campaign_service.py')
    seen = []

    def fake_run(command, **kw):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, '{"status": "pausing"}', '')

    monkeypatch.setattr(progress_app.subprocess, 'run', fake_run)
    assert json.loads(control('pause'))['status'] == 'pausing'
    assert seen[0] == ['wsl.exe', '-d', 'Ubuntu', '-e', '/venv/bin/python',
                       wsl_path(tmp_path / 'campaign_service.py'), '--run', wsl_path(run), 'pause']
    control('resume')
    assert seen[1][-1] == 'resume'
    # Nothing from a request reaches the command line.
    for bogus in ('stop', 'pause; rm -rf /', '--force', ''):
        with pytest.raises(ValueError):
            control(bogus)
    assert len(seen) == 2


def test_control_surfaces_failure_and_refuses_concurrent_commands(tmp_path, monkeypatch):
    control = Control(tmp_path, 'Ubuntu', '/venv/bin/python', tmp_path / 'campaign_service.py')
    monkeypatch.setattr(progress_app.subprocess, 'run',
                        lambda command, **kw: subprocess.CompletedProcess(command, 1, '', 'campaign.lock held'))
    with pytest.raises(RuntimeError, match='campaign.lock held'):
        control('pause')

    def timeout(command, **kw):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(progress_app.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError):
        control('resume')
    # A wedged command must not leave the control permanently locked.
    monkeypatch.setattr(progress_app.subprocess, 'run',
                        lambda command, **kw: subprocess.CompletedProcess(command, 0, 'ok', ''))
    assert control('pause') == 'ok'
    control.lock.acquire()
    with pytest.raises(RuntimeError, match='still running'):
        control('resume')


def test_wsl_path_maps_drives_passes_posix_through_and_refuses_unc():
    assert wsl_path('C:\\Code\\gameDecomp\\eval') == '/mnt/c/Code/gameDecomp/eval'
    assert wsl_path('c:/Code/x') == '/mnt/c/Code/x'
    assert wsl_path('/mnt/c/Code/x') == '/mnt/c/Code/x'
    with pytest.raises(ValueError):
        wsl_path('\\\\server\\share\\run')


def test_tail_tolerates_partial_lines_and_preserves_attempts(tmp_path):
    path=tmp_path/'pipeline.log'
    path.write_text('partial{\n'+json.dumps({'function':'same','score':10})+'\n'+
                    json.dumps({'function':'same','score':20})+'\n'+
                    json.dumps({'status':'paused_budget'})+'\nunfinished')
    rows=recent_results(path)
    assert [r['score'] for r in rows]==[20,10]


def test_service_running_overrides_budget_checkpoint_and_reports_stale_heartbeat(tmp_path):
    checkpoint={'kind':'campaign-checkpoint-index-v1','updated_at':98,'status':'paused_budget',
        'health':{'functions':20,'states':{'pending':15,'object_exact':5},
        'parallel_inflight':[{'id':'90000000000-test','function':'test','profile':{'name':'local_rewrites','model':False}}]}}
    (tmp_path/'campaign.json').write_text(json.dumps(checkpoint))
    (tmp_path/'service.json').write_text(json.dumps({'status':'running','worker_pid':123,'heartbeat_at':99}))
    value=snapshot(tmp_path,now=100)
    assert value['status']=='running'
    assert value['workers'][0]['elapsed_seconds']==10
    assert value['workers'][0]['model'] is False
    assert snapshot(tmp_path,now=150)['status']=='heartbeat_delayed'
    (tmp_path/'service.pause').touch()
    assert snapshot(tmp_path,now=150)['status']=='pausing'
def test_gpu_telemetry_and_missing_driver(monkeypatch):
    from eval import progress_app
    from types import SimpleNamespace
    monkeypatch.setattr(progress_app.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='NVIDIA GeForce RTX 5080, 87, 14900, 16303, 260.2, 360\n'))
    sample=progress_app.gpu_sample()
    assert sample['available'] and sample['utilization']==87 and sample['memory_used_mib']==14900
    def missing(*a,**k):raise FileNotFoundError('no GPU')
    monkeypatch.setattr(progress_app.subprocess,'run',missing)
    assert not progress_app.gpu_sample()['available']


def _log(path, rows, tail=''):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows) + tail, encoding='utf-8')


def test_repair_rows_carry_the_same_functions_previous_logged_score(tmp_path):
    from eval.progress_app import ScoreHistory
    path = tmp_path / 'pipeline.log'
    _log(path, [{'function': 'a', 'profile': 'intake', 'score': 10.0},
                {'function': 'b', 'profile': 'intake', 'score': 50.0},
                {'function': 'a', 'profile': 'local_rewrites', 'score': 12.5},
                {'function': 'b', 'profile': 'schema_patch', 'score': 49.0}])
    rows = recent_results(path, history=ScoreHistory())
    by = {(r['function'], r['profile']): r['previous_score'] for r in rows}
    assert by[('a', 'local_rewrites')] == 10.0       # not b's interleaved 50.0
    assert by[('b', 'schema_patch')] == 50.0
    assert by[('a', 'intake')] is None and by[('b', 'intake')] is None


def test_history_reads_only_appended_lines_and_waits_for_a_complete_line(tmp_path):
    from eval.progress_app import ScoreHistory
    path = tmp_path / 'pipeline.log'
    history = ScoreHistory()
    _log(path, [{'function': 'a', 'score': 10.0}], tail='{"function": "a", "sco')
    recent_results(path, history=history)
    assert [s for _, s in history.scores['a']] == [10.0]      # partial not counted
    with path.open('a', encoding='utf-8') as stream:
        stream.write('re": 20.0}\n' + json.dumps({'function': 'a', 'score': 30.0}) + '\n')
    rows = recent_results(path, history=history)
    assert [s for _, s in history.scores['a']] == [10.0, 20.0, 30.0]
    assert rows[0]['score'] == 30.0 and rows[0]['previous_score'] == 20.0


def test_non_ascii_bytes_do_not_shift_offsets_onto_the_wrong_row(tmp_path):
    from eval.progress_app import ScoreHistory
    path = tmp_path / 'pipeline.log'
    _log(path, [{'function': 'a', 'score': 1.0, 'note': '漢字 ─ ✓'},
                {'function': 'a', 'score': 2.0},
                {'function': 'a', 'score': 3.0}])
    rows = recent_results(path, history=ScoreHistory())
    assert [(r['score'], r['previous_score']) for r in rows] == [(3.0, 2.0), (2.0, 1.0), (1.0, None)]


def test_a_replaced_shorter_log_resets_the_history(tmp_path):
    from eval.progress_app import ScoreHistory
    path = tmp_path / 'pipeline.log'
    history = ScoreHistory()
    _log(path, [{'function': 'a', 'score': 10.0}, {'function': 'a', 'score': 20.0}])
    recent_results(path, history=history)
    _log(path, [{'function': 'a', 'score': 5.0}])
    rows = recent_results(path, history=history)
    assert rows[0]['previous_score'] is None and [s for _, s in history.scores['a']] == [5.0]

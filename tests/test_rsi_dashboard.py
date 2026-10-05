"""The narrow-RSI dashboard mode: read-only projection, one-shot start guard, bounded stop.

No server, no WSL, no runner process and no long job: every test drives the control plane
against a real temporary directory and asserts the contract the HTML panel consumes.

`tempfile.mkdtemp` rather than pytest's `tmp_path`: that fixture fails on this Windows box
with PermissionError, and this suite has to stay green here.
"""
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace

import pytest

from eval import progress_app
from eval.rsi_control import (LAUNCH_NAME, PANEL_FIELDS, PID_NAME, STATE_NAME, STOP_NAME,
                              RsiControl)

HERE = Path(progress_app.__file__).resolve().parent


def full_state(**overrides):
    """The complete state the contract describes, with one plausible value per key."""
    state = {
        'experiment_id': 'rsi-smoke-1',
        'stage': 'evaluate',
        'generation': 'S1',
        'parent': 'S0',
        'candidate': 'S1',
        'hypothesis': 'A narrow-locals repair transfers to the frozen dev panel.',
        'evidence_status': 'confirmed',
        'budget': {'caps': {'calls': 24, 'compiles': 72},
                   'spent': {'calls': 9, 'compiles': 21},
                   'remaining': {'calls': 15, 'compiles': 51}},
        'evaluation': {'panel': 'dev-panel-1', 'functions': 6, 'coverage': 1.0},
        'gate_reason': 'coverage complete and the candidate beat the parent by one match',
        'stop_requested': False,
        'updated_at': time.time(),
    }
    state.update(overrides)
    return state


@pytest.fixture
def run_dir():
    path = Path(tempfile.mkdtemp(prefix='rsi-dashboard-'))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_state(run_dir, value):
    (run_dir/STATE_NAME).write_text(json.dumps(value), encoding='utf-8')


def write_json(run_dir, name, value):
    (run_dir/name).write_text(json.dumps(value), encoding='utf-8')


def forbidden(*args, **kwargs):
    pytest.fail('a test with no run in flight tried to launch or signal a process')


# (a) ---------------------------------------------------------------------------
def test_missing_state_file_renders_the_idle_path_without_raising(run_dir):
    state = RsiControl(run_dir, launch=False).get()
    assert state['present'] is False
    assert state['stage'] == 'idle'
    assert state['active'] is False and state['terminal'] is False
    assert state['hypothesis'] == '' and state['gate_reason'] == ''
    assert state['evidence_status'] == 'unknown'
    assert state['budget'] == {'caps': {}, 'spent': {}, 'remaining': {}, 'kinds': []}
    assert state['evaluation'] == {'panel': None, 'functions': None, 'coverage': None}
    assert state['last_error'] is None and state['stop_pending'] is False
    assert 'No narrow-RSI experiment' in state['phase']
    assert set(PANEL_FIELDS) <= set(state)


@pytest.mark.parametrize('value', [{}, {'stage': 'research'}, {'budget': None, 'evaluation': 7},
                                   {'stage': 3, 'generation': [], 'updated_at': 'soon'},
                                   {'stage': 'done', 'budget': {'spent': {'calls': None}}}])
def test_partial_or_odd_state_never_raises_and_still_carries_every_field(run_dir, value):
    write_state(run_dir, value)
    state = RsiControl(run_dir, launch=False).get()
    assert state['present'] is True
    assert set(PANEL_FIELDS) <= set(state)
    assert isinstance(state['budget']['kinds'], list)
    assert state['updated_at'] is None or isinstance(state['updated_at'], float)


# (b) ---------------------------------------------------------------------------
def test_complete_state_exposes_every_field_the_panel_needs(run_dir):
    value = full_state()
    write_state(run_dir, value)
    state = RsiControl(run_dir, launch=False).get()
    assert set(PANEL_FIELDS) <= set(state)
    assert state['present'] is True and state['active'] is True and state['stale'] is False
    assert state['experiment_id'] == 'rsi-smoke-1'
    assert (state['stage'], state['generation']) == ('evaluate', 'S1')
    assert (state['parent'], state['candidate']) == ('S0', 'S1')
    assert state['hypothesis'] == value['hypothesis']
    assert state['evidence_status'] == 'confirmed'
    assert state['budget']['caps'] == {'calls': 24, 'compiles': 72}
    assert state['budget']['spent'] == {'calls': 9, 'compiles': 21}
    assert state['budget']['remaining'] == {'calls': 15, 'compiles': 51}
    assert state['budget']['kinds'] == ['calls', 'compiles']
    assert state['evaluation'] == {'panel': 'dev-panel-1', 'functions': 6, 'coverage': 1.0}
    assert state['gate_reason'] == value['gate_reason']
    assert state['stop_requested'] is False and state['stop_pending'] is False
    assert state['updated_at'] == value['updated_at'] and state['age_seconds'] < 60
    assert state['controls_enabled'] is True


def test_a_terminal_stage_is_not_running_and_a_null_candidate_stays_null(run_dir):
    write_state(run_dir, full_state(stage='keep-parent', candidate=None, evidence_status='refuted'))
    state = RsiControl(run_dir, launch=False).get()
    assert state['active'] is False and state['terminal'] is True
    assert state['candidate'] is None and state['parent'] == 'S0'
    assert 'parent is kept' in state['phase']


def test_missing_remaining_is_derived_from_cap_minus_spent_and_never_negative(run_dir):
    write_state(run_dir, full_state(budget={'caps': {'calls': 24}, 'spent': {'calls': 30}}))
    state = RsiControl(run_dir, launch=False).get()
    assert state['budget']['remaining'] == {'calls': 0}


# (c) ---------------------------------------------------------------------------
def test_start_refuses_while_a_run_is_already_marked_running(run_dir, monkeypatch):
    monkeypatch.setattr('eval.rsi_control.subprocess.Popen', forbidden)
    write_state(run_dir, full_state(stage='train'))
    control = RsiControl(run_dir)
    assert control.get()['active'] is True
    with pytest.raises(RuntimeError, match='already running'):
        control('start', {})
    with pytest.raises(RuntimeError, match='already running'):
        control('start', {'minutes': 5})
    assert control.live_process() is False


def test_start_declares_one_background_command_and_refuses_a_missing_runner(run_dir, monkeypatch):
    monkeypatch.setattr('eval.rsi_control.subprocess.Popen', forbidden)
    plan = RsiControl(run_dir, launch=False)('start', {})
    assert plan['launched'] is False and plan['minutes'] == 30 and plan['run_id']
    command = plan['command']
    assert command[command.index('-m')+1] == 'eval.narrow_rsi'
    for flag in ('--state', '--stop', '--pid', '--run-id', '--minutes'):
        assert flag in command
    assert any('rsi_state.json' in token for token in command)
    # The runner module is not in this checkout yet: that is reported, never spawned.
    with pytest.raises(ValueError, match='not present'):
        RsiControl(run_dir, runner=run_dir/'no-such-runner.py')('start', {})


@pytest.mark.parametrize('options', [{'minutes': 0}, {'minutes': 121}, {'minutes': True},
                                     {'minutes': '30'}, {'experiment_id': 'other'}])
def test_bad_start_options_launch_nothing(run_dir, monkeypatch, options):
    monkeypatch.setattr('eval.rsi_control.subprocess.Popen', forbidden)
    with pytest.raises(ValueError):
        RsiControl(run_dir)('start', options)


def test_an_old_heartbeat_frees_start_unless_a_recorded_group_is_still_alive(run_dir, monkeypatch):
    monkeypatch.setattr('eval.rsi_control.subprocess.Popen', forbidden)
    write_state(run_dir, full_state(stage='train', updated_at=1))
    stale = RsiControl(run_dir)
    assert stale.get()['stale'] is True and stale.get()['active'] is False
    assert RsiControl(run_dir, launch=False)('start', {})['launched'] is False
    # A group this dashboard launched, still alive, outvotes the old heartbeat.
    write_json(run_dir, LAUNCH_NAME, {'run_id': 'run-1'})
    write_json(run_dir, PID_NAME, {'run_id': 'run-1', 'pid': 4321, 'pgid': 4321})
    live = RsiControl(run_dir)
    monkeypatch.setattr(live, 'group_alive', lambda pgid: True)
    assert live.get()['stale'] is False and live.get()['active'] is True
    with pytest.raises(RuntimeError, match='already running'):
        live('start', {})


# (d) ---------------------------------------------------------------------------
def test_stop_writes_the_stop_file_and_does_not_raise_when_no_pid_is_known(run_dir, monkeypatch):
    monkeypatch.setattr('eval.rsi_control.subprocess.run', forbidden)
    write_state(run_dir, full_state(stage='train'))
    control = RsiControl(run_dir, launch=False)
    state = control('stop', {})
    request = json.loads((run_dir/STOP_NAME).read_text(encoding='utf-8'))
    assert request['run_id'] == 'rsi-smoke-1' and request['requested_at'] > 0
    assert control.last_signals == []
    assert state['stop_pending'] is True and state['active'] is True
    assert 'Stop requested' in state['phase']


def test_stop_never_signals_a_group_that_belongs_to_another_run(run_dir, monkeypatch):
    monkeypatch.setattr('eval.rsi_control.subprocess.run', forbidden)
    write_state(run_dir, full_state(stage='train'))
    control = RsiControl(run_dir, grace=0)
    sent, seen = [], []

    def alive_once(pgid):
        seen.append(pgid)
        return len(seen) < 2

    monkeypatch.setattr(control, 'group_alive', alive_once)
    monkeypatch.setattr(control, 'send_group_signal', lambda pgid, name: sent.append((pgid, name)))
    write_json(run_dir, LAUNCH_NAME, {'run_id': 'run-1'})
    write_json(run_dir, PID_NAME, {'run_id': 'run-2', 'pid': 999, 'pgid': 999})
    control('stop', {})
    assert sent == [] and seen == []
    write_json(run_dir, PID_NAME, {'run_id': 'run-1', 'pid': 4321, 'pgid': 4321})
    control('stop', {})
    assert sent == [(4321, 'TERM')]


def test_launch_clears_a_previous_runs_stop_file_and_records_what_it_started(run_dir, monkeypatch):
    """The real launch path, with the process itself stubbed out."""
    started = []

    def spawn(command, **kwargs):
        started.append(command)
        return SimpleNamespace(pid=4321, poll=lambda: None)

    monkeypatch.setattr('eval.rsi_control.subprocess.Popen', spawn)
    runner = run_dir/'declared-runner.py'
    runner.write_text('# the declared runner, for this test only\n', encoding='utf-8')
    (run_dir/STOP_NAME).write_text('{}', encoding='utf-8')
    control = RsiControl(run_dir, runner=runner, enabled=True)
    control('start', {'minutes': 5})
    assert len(started) == 1 and started[0][started[0].index('-m')+1] == 'eval.narrow_rsi'
    assert not (run_dir/STOP_NAME).exists()      # the new run must not inherit the old stop
    launch = json.loads((run_dir/LAUNCH_NAME).read_text(encoding='utf-8'))
    assert launch['launcher_pid'] == 4321 and launch['minutes'] == 5
    with pytest.raises(RuntimeError, match='already running'):
        control('start', {})                     # our own live process is the second guard
    assert len(started) == 1


def test_read_only_mode_refuses_control_and_says_so_in_the_projection(run_dir):
    control = RsiControl(run_dir, enabled=False, launch=False)
    assert control.get()['controls_enabled'] is False
    with pytest.raises(ValueError, match='read-only'):
        control('start', {})
    with pytest.raises(ValueError, match='read-only'):
        control('stop', {})


# --- wiring: the app serves the mode and the panel reads exactly these fields ----
class Response:
    """Exactly the parts of BaseHTTPRequestHandler the routes touch: no socket, no server."""

    def __init__(self):
        self.status, self.headers, self.wfile = None, {}, io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass

    def send_error(self, code, message=None):
        self.status = code


def call(route, *, rsi=None, method='GET', body=b'', headers=None):
    Handler = progress_app.handler(SimpleNamespace(get=lambda: {'app': 'gameDecomp-progress'}),
                                   None, None, None, rsi)
    request, response = object.__new__(Handler), Response()
    request.path, request.command = route, method
    request.headers, request.rfile = headers or {}, io.BytesIO(body)
    request.server = SimpleNamespace(server_address=('127.0.0.1', 8765))
    request.wfile = response.wfile
    for name in ('send_response', 'send_header', 'end_headers', 'send_error'):
        setattr(request, name, getattr(response, name))
    getattr(request, 'do_' + method)()
    return response.status, response.wfile.getvalue()


def test_the_read_only_endpoint_serves_the_state_or_reports_it_absent(run_dir):
    status, body = call('/api/rsi', rsi=RsiControl(run_dir, launch=False))
    assert status == 200 and json.loads(body)['present'] is False
    write_state(run_dir, full_state())
    status, body = call('/api/rsi', rsi=RsiControl(run_dir, launch=False))
    assert status == 200 and json.loads(body)['experiment_id'] == 'rsi-smoke-1'
    status, body = call('/api/rsi')
    assert status == 200 and json.loads(body) == {'present': False, 'controls_enabled': False}


def test_the_control_route_stops_through_the_stop_file(run_dir):
    write_state(run_dir, full_state(stage='train'))
    control = RsiControl(run_dir, launch=False)
    body = b'{"action":"stop"}'
    status, payload = call('/api/rsi/control', rsi=control, method='POST', body=body,
                           headers={'X-Campaign-Control': 'ui',
                                    'Content-Length': str(len(body))})
    assert status == 200 and json.loads(payload)['stop_pending'] is True
    assert (run_dir/STOP_NAME).exists()
    for refused in (RsiControl(run_dir, enabled=False, launch=False), control):
        for headers in ({}, {'X-Campaign-Control': 'ui', 'Origin': 'https://example.org'}):
            status, _ = call('/api/rsi/control', rsi=refused, method='POST', body=body,
                             headers={**headers, 'Content-Length': str(len(body))})
            assert status == 403


def test_the_app_declares_the_read_only_route_the_control_route_and_the_panel_script():
    source = (HERE/'progress_app.py').read_text(encoding='utf-8')
    assert "route == '/api/rsi'" in source
    assert "'/api/rsi/control'" in source
    assert "route == '/progress_rsi.js'" in source
    assert 'rsi.get()' in source


def test_the_panel_is_hidden_until_relevant_and_reads_only_declared_elements():
    html = (HERE/'progress_app.html').read_text(encoding='utf-8')
    script = (HERE/'progress_rsi.js').read_text(encoding='utf-8')
    assert '<section id="rsi-panel" class="panel rsi-panel hidden"' in html
    assert "<script src=\"/progress_rsi.js\"></script>" in html
    for element in ('rsi-panel', 'rsi-status', 'rsi-phase', 'rsi-stage', 'rsi-generation',
                    'rsi-evidence', 'rsi-coverage', 'rsi-panel-note', 'rsi-hypothesis',
                    'rsi-budget', 'rsi-gate', 'rsi-error', 'rsi-start', 'rsi-stop',
                    'rsi-minutes'):
        assert f'id="{element}"' in html and f"'{element}'" in script
    assert "fetch('/api/rsi'" in script and "fetch('/api/rsi/control'" in script
    # Runner output is text, never markup.
    assert 'innerHTML' not in script

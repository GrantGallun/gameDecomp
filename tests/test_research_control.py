import json
import threading
import time
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eval.progress_app import handler
from eval.research_control import ResearchControl


def test_start_is_bounded_duplicate_safe_and_stop_targets_this_run(tmp_path, monkeypatch):
    seen = []
    def start(argv, **kw):
        seen.append(argv)
        return SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr('eval.research_control.subprocess.Popen', start)
    c = ResearchControl(tmp_path)
    state = c('start', {'minutes': 5, 'max_calls': 2})
    assert state['active'] and state['status'] == 'starting'
    assert '--max-calls' in seen[0] and '2' in seen[0]
    with pytest.raises(RuntimeError, match='already'):
        c('start', {})
    assert len(seen) == 1
    c('stop', {})
    assert json.loads((tmp_path/'stop.json').read_text())['run_id'] == state['run_id']


@pytest.mark.parametrize('options', [{'minutes': 0}, {'minutes': 121}, {'max_calls': 500},
    {'max_calls': True}, {'model': 'deepseek-cloud'}, {'endpoint': 'https://paid.example'},
    {'repo': '/tmp/other'}, {'minutes': '30'}])
def test_bad_start_options_never_launch_a_worker(tmp_path, options, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail('Invalid request launched a process')
    monkeypatch.setattr('eval.research_control.subprocess.Popen', forbidden)
    with pytest.raises(ValueError):
        ResearchControl(tmp_path)('start', options)


def test_dashboard_restart_recognizes_worker_and_stale_heartbeat(tmp_path):
    c = ResearchControl(tmp_path)
    (tmp_path/'status.json').write_text(json.dumps(dict(status='running', run_id='old', updated_at=time.time())))
    assert c.get()['active']
    with pytest.raises(RuntimeError):
        c('start', {})
    (tmp_path/'status.json').write_text(json.dumps(dict(status='running', run_id='old', updated_at=1)))
    assert c.get()['status'] == 'interrupted'
    assert not c.get()['active']  # worker's Linux lock remains the final duplicate guard


def test_rejected_duplicate_launch_cannot_hide_an_existing_live_worker(tmp_path):
    c = ResearchControl(tmp_path)
    (tmp_path/'status.json').write_text(json.dumps(dict(status='running', run_id='live', updated_at=time.time())))
    c.process = SimpleNamespace(poll=lambda: 2)
    c.pending = dict(status='starting', run_id='rejected', updated_at=time.time())
    assert c.get()['run_id'] == 'live'
    c('stop', {})
    assert json.loads((tmp_path/'stop.json').read_text())['run_id'] == 'live'


@pytest.fixture
def server(tmp_path):
    def unavailable():
        raise ValueError('No campaign data')
    lab = ResearchControl(tmp_path, enabled=False)
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler(SimpleNamespace(get=unavailable), research=lab))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{http.server_port}', lab
    http.shutdown()
    http.server_close()
    thread.join()


def test_research_and_health_work_without_a_campaign_and_read_only_disables_start(server):
    base, lab = server
    with urlopen(base+'/api/health') as response:
        assert json.load(response)['app'] == 'gameDecomp-progress'
    with urlopen(base+'/api/research') as response:
        assert json.load(response)['controls_enabled'] is False
    request = Request(base+'/api/research/control', data=b'{"action":"start"}',
                      headers={'X-Campaign-Control': 'ui', 'Content-Type': 'application/json'})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403


def test_research_control_refuses_cross_origin_and_non_object_bodies(server):
    base, lab = server
    lab.enabled = True
    for headers, body, expected in [({}, b'{}', 403),
        ({'X-Campaign-Control': 'ui', 'Origin': 'https://example.org'}, b'{}', 403),
        ({'X-Campaign-Control': 'ui'}, b'[]', 400)]:
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base+'/api/research/control', data=body, headers=headers))
        assert error.value.code == expected


def test_research_route_uses_separate_controller(server):
    base, lab = server
    lab.enabled = True
    with urlopen(Request(base+'/api/research/control', data=b'{"action":"stop"}',
                        headers={'X-Campaign-Control': 'ui'})) as response:
        assert json.load(response)['active'] is False

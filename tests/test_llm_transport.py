import json
import pytest
from solver import llm


def test_explicit_single_transport_budget_preserves_timeout_receipt(monkeypatch):
    calls = []
    def send(request, timeout):
        calls.append(timeout)
        raise TimeoutError('pilot timeout')
    monkeypatch.setattr(llm.urllib.request, 'urlopen', send)
    monkeypatch.setattr(llm.time, 'sleep', lambda seconds: pytest.fail('single attempt must not back off'))
    with pytest.raises(TimeoutError) as caught:
        llm.generate('http://local', 'fake', 'prompt', timeout=45, transport_attempts=1)
    assert calls == [45]
    assert len(caught.value.transport_events) == 1
    with pytest.raises(ValueError, match='transport_attempts'):
        llm.generate('http://local', 'fake', 'prompt', transport_attempts=0)


def test_timeout_records_each_transport_attempt_without_changing_exception(monkeypatch):
    calls=[]
    sleeps=[]
    def send(request,timeout):
        calls.append(timeout)
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(llm.urllib.request,'urlopen',send)
    monkeypatch.setattr(llm.time,'sleep',sleeps.append)
    with pytest.raises(TimeoutError) as caught:
        llm.generate('http://local','fake','prompt',timeout=240)
    events=caught.value.transport_events
    assert calls==[240,240,240] and sleeps==[2.0,4.0]
    assert [row['attempt'] for row in events]==[1,2,3]
    assert all(row['error_type']=='TimeoutError' and row['status']=='error' for row in events)


def test_success_and_cache_distinguish_current_from_original_requests(tmp_path,monkeypatch):
    count=[]
    class Response:
        """One body, then EOF. A real `http.client.HTTPResponse.read(amt)` returns b'' when the body
        is exhausted; this stub used to return the same bytes on every call, which is not a response
        any client can consume and which the chunked reader correctly refused to treat as an ending.
        """
        def __init__(self): self._body = json.dumps({'response':'answer','done':True}).encode()
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self, amt=None):
            body, self._body = self._body, b""
            return body
    def send(request,timeout):
        count.append(1)
        if len(count)==1: raise TimeoutError('transient')
        return Response()
    monkeypatch.setattr(llm.urllib.request,'urlopen',send)
    monkeypatch.setattr(llm.time,'sleep',lambda seconds:None)
    kwargs=dict(endpoint='http://local',model='fake',prompt='prompt',seed=1,cache_dir=tmp_path)
    _,meta=llm.generate(**kwargs)
    assert [row['status'] for row in meta['_transport_events']]==['error','response']
    _,cached=llm.generate(**kwargs)
    assert cached['_cache_hit'] and cached['_transport_events']==[]
    assert len(cached['_cached_transport_events'])==2 and len(count)==2


# --- the wall-clock bound ------------------------------------------------------
#
# `urlopen(req, timeout=T)` sets a PER-SOCKET-OPERATION timeout; it bounds connect and each recv, not
# the request. `resp.read()` reads to EOF, so a server that sends one byte inside every T-second
# window holds the call open indefinitely while the parameter reads as an overall bound.
#
# This is not hypothetical: on 2026-09-16 an admission re-run sat inside one model call for over
# twenty minutes with timeout=300, then again with --timeout 240, emitting nothing. It looked exactly
# like a slow function or a blocked workspace flock until a step trace separated them.

class _Trickle:
    """A response that never ends and never stops sending: the shape that defeats a socket timeout."""

    def __init__(self): self.reads = 0
    def read(self, amt=None):
        self.reads += 1
        return b" "          # never b"", so an unbounded loop never terminates


def test_wall_clock_deadline_fires_on_a_stream_that_never_ends():
    reader = _Trickle()
    started = llm.time.monotonic()
    with pytest.raises(TimeoutError, match='wall-clock budget'):
        llm._read_body(reader, deadline=started + 0.05, started=started)
    assert reader.reads > 0, "the reader was never consulted, so this proves nothing"


def test_wall_clock_deadline_does_not_fire_on_a_normal_body():
    class Once:
        def __init__(self): self.sent = False
        def read(self, amt=None):
            if self.sent: return b""
            self.sent = True
            return b'{"response":"ok","done":true}'
    started = llm.time.monotonic()
    body = llm._read_body(Once(), deadline=started + 5.0, started=started)
    assert json.loads(body)['response'] == 'ok'


def test_empty_body_returns_empty_rather_than_hanging():
    class Empty:
        def read(self, amt=None): return b""
    started = llm.time.monotonic()
    assert llm._read_body(Empty(), deadline=started + 5.0, started=started) == b""

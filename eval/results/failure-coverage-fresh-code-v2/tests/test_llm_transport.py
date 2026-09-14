import json
import pytest
from solver import llm


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
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps({'response':'answer','done':True}).encode()
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

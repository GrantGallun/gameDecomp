import json

import pytest

from solver import llm


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amt=None):
        # One body, then EOF, like a real http.client.HTTPResponse. Returning the same bytes forever
        # is not a response any client can consume: the chunked reader in solver/llm.py correctly
        # treats b"" as the only end, and a stub that never ends hangs instead of failing.
        if self.payload is None:
            return b""
        payload = json.dumps(self.payload).encode()
        self.payload = None
        return payload


def test_gpt_oss_emission_uses_low_and_schema_is_cache_bound(tmp_path, monkeypatch):
    requests = []
    def send(request, timeout):
        requests.append(json.loads(request.data))
        return _Response({'response': '{}', 'done_reason': 'stop'})
    monkeypatch.setattr(llm.urllib.request, 'urlopen', send)
    kwargs = dict(endpoint='http://local', model='gpt-oss:20b', prompt='emit JSON',
                  think='false', seed=3, cache_dir=tmp_path)
    _, plain = llm.generate(**kwargs)
    schema = {'type': 'object'}
    _, structured = llm.generate(**kwargs, response_schema=schema)
    assert requests[0]['think'] == requests[1]['think'] == 'low'
    assert requests[1]['format'] == schema
    assert requests[1]['messages'] == [{'role': 'user', 'content': 'emit JSON'}]
    assert 'prompt' not in requests[1]
    assert plain['_cache_key'] != structured['_cache_key']


def test_seeded_generation_cache_reuses_only_the_same_draw(tmp_path,
                                                          monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        requests.append((body, timeout))
        seed = body["options"]["seed"]
        return _Response({
            "response": f"answer-{seed}",
            "eval_count": 12,
            "done_reason": "stop",
        })

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    kwargs = {
        "endpoint": "http://local",
        "model": "fake",
        "prompt": "same prompt",
        "temperature": 0.5,
        "num_predict": 100,
        "seed": 7,
        "cache_dir": tmp_path,
    }
    text1, meta1 = llm.generate(**kwargs)
    text2, meta2 = llm.generate(**kwargs)

    assert text1 == text2 == "answer-7"
    assert len(requests) == 1
    assert requests[0][0]["options"]["seed"] == 7
    assert meta1["_cache_hit"] is False
    assert meta2["_cache_hit"] is True
    assert meta1["_cache_key"] == meta2["_cache_key"]

    text3, meta3 = llm.generate(**(kwargs | {"seed": 8}))
    assert text3 == "answer-8"
    assert meta3["_cache_hit"] is False
    assert len(requests) == 2, "a new seed must always generate a new draw"


def test_cache_requires_explicit_seed(tmp_path):
    with pytest.raises(ValueError, match="explicit seed"):
        llm.generate("http://unused", "fake", "prompt", cache_dir=tmp_path)


def test_fixed_context_is_cache_bound_and_preserves_headroom(tmp_path,monkeypatch):
    requests=[]
    def send(request,timeout):
        requests.append(json.loads(request.data))
        return _Response({'response':'answer','eval_count':2})
    monkeypatch.setattr(llm.urllib.request,'urlopen',send)
    kwargs=dict(endpoint='http://local',model='fake',prompt='small',seed=1,cache_dir=tmp_path)
    _, adaptive=llm.generate(**kwargs)
    _, fixed=llm.generate(**kwargs,num_ctx=32768)
    _, cached=llm.generate(**kwargs,num_ctx=32768)
    assert [r['options']['num_ctx'] for r in requests]==[8192,32768]
    assert fixed['_request_options']['num_ctx']==32768
    assert adaptive['_cache_key']!=fixed['_cache_key'] and cached['_cache_hit']
    for value in (0,4096,65536,True,32768.0):
        with pytest.raises(ValueError,match='num_ctx'):llm.generate(**kwargs,num_ctx=value)
    with pytest.raises(ValueError,match='num_ctx'):
        llm.generate('http://unused','fake','x'*50000,num_ctx=16384)


def test_cache_only_never_calls_endpoint_on_miss(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("cache-only mode touched the endpoint")

    monkeypatch.setattr(llm.urllib.request, "urlopen", forbidden)
    with pytest.raises(FileNotFoundError, match="cache-only mode"):
        llm.generate("http://unused", "fake", "prompt", seed=3,
                     cache_dir=tmp_path, cache_only=True)


def test_cache_only_requires_cache_directory():
    with pytest.raises(ValueError, match="requires cache_dir"):
        llm.generate("http://unused", "fake", "prompt", seed=3,
                     cache_only=True)


def test_behavior_changing_flags_get_distinct_cache_keys(tmp_path,
                                                         monkeypatch):
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        return _Response({"response": "ok", "eval_count": 1,
                          "done_reason": "stop"})

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    base = dict(endpoint="http://local", model="fake", prompt="p", seed=1,
                cache_dir=tmp_path)
    _, low = llm.generate(**base, think="low")
    _, false = llm.generate(**base, think="false")
    assert calls == 2
    assert low["_cache_key"] != false["_cache_key"]

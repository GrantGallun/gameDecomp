import json

import pytest

from eval import agentrepair


def test_compact_streamed_checkpoint_preserves_all_data(tmp_path):
    path = tmp_path / 'checkpoint.json'
    value = {'source': 'snowman \u2603', 'traces': [{'values': list(range(100))}]}
    agentrepair._atomic_json(path, value)
    encoded = path.read_text(encoding='utf-8')
    assert json.loads(encoded) == value
    assert encoded == json.dumps(value, separators=(',', ':')) + '\n'


def test_failed_stream_does_not_replace_valid_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / 'checkpoint.json'
    agentrepair._atomic_json(path, {'completed': 10})
    def fail(value, stream, **kwargs):
        stream.write('{"partial":')
        raise OSError(12, 'Cannot allocate memory')
    monkeypatch.setattr(agentrepair.json, 'dump', fail)
    with pytest.raises(OSError):
        agentrepair._atomic_json(path, {'completed': 11})
    assert json.loads(path.read_text()) == {'completed': 10}

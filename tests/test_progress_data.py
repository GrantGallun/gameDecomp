"""Dashboard data metrics are recorded metadata, not repeated catalog reads."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.request import urlopen

from eval.campaign_state import Store
from eval.progress_app import handler
from eval.progress_map import MapFeed
from http.server import ThreadingHTTPServer


def state():
    return {'nodes': {'f': {'status': 'pending', 'size': 12}},
            'binary_data_catalog': {'catalog_sha256': 'a'*64, 'input_sha256': 'b'*64,
                'sha256': 'c'*64, 'path': 'binary-data/must-not-be-read.json',
                'summary': {'regions': 1567, 'rom_verified_bytes': 194524,
                    'reconstructed_bytes': 189356, 'unbacked_bss_bytes': 514752,
                    'typed_c_verified_bytes': 0, 'functions_with_data_context': 1526,
                    'functions_with_readonly_inputs': 175,
                    'readonly_initial_bytes': 9025, 'mutable_initial_bytes': 185499,
                    'address_references': 1527, 'rom_bytes': 8388608,
                    'private_large_field': 'not sent'}}}


def test_recorded_measures_stay_separate_without_catalog_file_polling(tmp_path, monkeypatch):
    Store(tmp_path/'campaign.json').save(state())
    original = Path.read_bytes
    reads = []

    def guarded(path):
        reads.append(path)
        assert path == tmp_path/'campaign.json'
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', guarded)
    feed = MapFeed(tmp_path)
    result = feed.data()
    assert result['status'] == 'recorded'
    assert result['summary']['rom_verified_bytes'] == 194524
    assert result['summary']['reconstructed_bytes'] == 189356
    assert result['summary']['unbacked_bss_bytes'] == 514752
    assert result['summary']['typed_c_verified_bytes'] == 0
    assert 'private_large_field' not in result['summary']
    assert 'percent' not in result
    assert feed.get()['exact_bytes'] == 0
    assert reads == [tmp_path/'campaign.json']
    assert feed.data() == result  # cached metadata; no artifact reads


def test_metadata_changes_refresh_without_reclassifying_functions(tmp_path):
    store = Store(tmp_path/'campaign.json')
    snapshot = state()
    store.save(snapshot)
    feed = MapFeed(tmp_path)
    before = feed.data()
    snapshot['binary_data_catalog']['summary']['functions_with_readonly_inputs'] = 176
    store.save(snapshot, changed=[])
    feed.last = 0
    after = feed.data()
    assert after['commit'] != before['commit']
    assert after['summary']['functions_with_readonly_inputs'] == 176
    assert feed.get('f')['status'] == 'pending'


def test_missing_catalog_is_unknown_and_invalid_hashes_hide_recorded_claims(tmp_path):
    store = Store(tmp_path/'campaign.json')
    store.save({'nodes': {}})
    assert MapFeed(tmp_path).data()['status'] == 'not_built'
    snapshot = state()
    snapshot['binary_data_catalog']['catalog_sha256'] = 'bad'
    store.save(snapshot)
    result = MapFeed(tmp_path).data()
    assert result['status'] == 'unavailable' and result['summary'] == {}


def test_invalid_counts_are_omitted_instead_of_becoming_verified_zeros(tmp_path):
    snapshot = state()
    snapshot['binary_data_catalog']['summary'].update(typed_c_verified_bytes=True,
        rom_verified_bytes=-1, reconstructed_bytes='194524')
    Store(tmp_path/'campaign.json').save(snapshot)
    summary = MapFeed(tmp_path).data()['summary']
    assert not {'typed_c_verified_bytes','rom_verified_bytes','reconstructed_bytes'} & summary.keys()


def test_http_data_and_script_routes_preserve_existing_panel_assets(tmp_path):
    Store(tmp_path/'campaign.json').save(state())
    feed = SimpleNamespace(map=MapFeed(tmp_path))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler(feed))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = 'http://127.0.0.1:' + str(server.server_port)
        with urlopen(base+'/api/data', timeout=3) as response:
            result = json.load(response)
            assert response.headers['Cache-Control'] == 'no-store'
            assert result['summary']['typed_c_verified_bytes'] == 0
        with urlopen(base+'/progress_data.js', timeout=3) as response:
            assert b"fetch('/api/data'" in response.read()
        with urlopen(base+'/', timeout=3) as response:
            html = response.read().decode()
            assert 'id="data-title"' in html
            for asset in ('progress_map.js','progress_integration.js','progress_runtime.js','progress_data.js'):
                assert '/'+asset in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

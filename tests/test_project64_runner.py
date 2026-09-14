"""Runner lifecycle tests use synthetic exports and fake processes, never game input."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from eval import project64_runner as r
from solver import project64_capture as p


@pytest.fixture
def job(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    assets = {}
    for name, value in [('Project64.exe', b'fixture executable'), ('Config/Project64.rdb', b'fixture rdb')]:
        path = bundle / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(value)
        assets[name] = r._sha(value)
    resources = tmp_path / 'resources'
    resources.mkdir()
    manifest = json.dumps(assets).encode()
    template = b'// synthetic exporter template'
    (resources / 'project64_assets.json').write_bytes(manifest)
    (resources / 'project64_capture_entry.js').write_bytes(template)
    monkeypatch.setattr(r, '__file__', str(resources / 'project64_runner.py'))
    monkeypatch.setattr(r, 'ASSETS_SHA256', r._sha(manifest))
    monkeypatch.setattr(r, 'TEMPLATE_SHA256', r._sha(template))
    monkeypatch.setattr(r, 'EXE_SHA256', assets['Project64.exe'])
    monkeypatch.setattr(r, '_is_windows', lambda: True)
    monkeypatch.setattr(r, '_existing_emulators', lambda: [])
    rom = tmp_path / 'original.z64'
    image = bytes.fromhex('80371240') + bytes(60) + bytes.fromhex('03e0000800000000')
    rom.write_bytes(image)
    plan = {'architecture': 'mips-o32-be', 'byte_order': 'big', 'function': 'fixture',
            'entry': 0x80000100, 'code_size': 8, 'rom_offset': 64,
            'rom_sha256': hashlib.sha256(image).hexdigest(), 'registers': p.register_map(),
            'ram': [{'name': 'fixture', 'kind': 'persistent', 'address': 0x80000200, 'size': 4}]}
    return {'schema_version': 1, 'plan': plan, 'rom_path': str(rom),
            'portable_dir': str(bundle), 'output_dir': str(tmp_path / 'output'),
            'selector': 'first', 'timeout_seconds': 10}


class Process:
    pid = 987654
    returncode = None
    terminated = False
    def poll(self): return self.returncode
    def terminate(self):
        self.terminated = True
        self.returncode = 1
    def wait(self, timeout): return self.returncode


def fake_capture(job, output, a0=0):
    plan = job['plan']
    image = Path(job['rom_path']).read_bytes()
    sample = {'paused': True, 'pc': plan['entry'], 'hi': 0, 'lo': 0, 'uhi': 0, 'ulo': 0,
              'gpr': [0] * 32, 'ugpr': [0] * 32,
              'code_hex': image[plan['rom_offset']:plan['rom_offset'] + plan['code_size']].hex(),
              'memory': [{**plan['ram'][0], 'hex': '01020304'}]}
    sample['gpr'][4] = a0
    raw = {'schema_version': 1, 'kind': 'project64-debug-paused-export',
           'producer_revision': p.REVISION, 'entry': plan['entry'],
           'rom_info': {'crc1': 0, 'crc2': 0}, 'samples': [sample, copy.deepcopy(sample)]}
    r._write(output / 'project64-entry-raw.json', raw)
    return 'captured', {'stage': 'captured'}


def test_real_resource_files_remain_exactly_pinned():
    root = Path(r.__file__).parent
    assert r._sha((root / 'project64_assets.json').read_bytes()) == r.ASSETS_SHA256
    assert r._sha((root / 'project64_capture_entry.js').read_bytes()) == r.TEMPLATE_SHA256
    assert json.loads((root / 'project64_assets.json').read_bytes())['Project64.exe'] == r.EXE_SHA256


def test_success_is_fresh_muted_verified_and_cleans_owned_handle(job, monkeypatch):
    process = Process()
    source_files = {p: p.read_bytes() for p in Path(job['portable_dir']).rglob('*') if p.is_file()}
    monkeypatch.setattr(r, '_launch', lambda exe, rom: process)
    monkeypatch.setattr(r, '_collect', lambda proc, out, deadline: fake_capture(job, out))
    result = r.run_job(job)
    assert result['status'] == 'captured'
    assert process.terminated and result['cleanup']['owned_pid'] == process.pid
    assert result['cleanup']['exited']
    assert r._read(Path(result['capture_path']))['kind'] == 'project64-stopped-entry-capture'
    output = Path(job['output_dir'])
    config = (output / 'portable/Config/Project64.cfg').read_text()
    assert '[Audio-Settings]\nVolume=0' in config
    assert 'Force Interpreter CPU=1' in config
    assert (output / 'portable/Scripts/capture-entry.js').read_text().startswith('var captureJob = ')
    assert source_files == {p: p.read_bytes() for p in source_files}
    assert r._read(output / 'receipt.json') == result
    saved = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError): r.run_job(job)
    assert saved == {p: p.read_bytes() for p in saved}


def test_existing_emulator_never_launched_or_stopped(job, monkeypatch):
    monkeypatch.setattr(r, '_existing_emulators', lambda: ['1234'])
    monkeypatch.setattr(r, '_launch', lambda *args: pytest.fail('must not launch'))
    result = r.run_job(job)
    assert result['status'] == 'blocked_existing_emulator'
    assert result['cleanup']['owned_pid'] is None
    assert not (Path(job['output_dir']) / 'portable').exists()


@pytest.mark.parametrize('status', ['timeout', 'export_error', 'emulator_exited'])
def test_every_collection_failure_cleans_owned_process(job, monkeypatch, status):
    process = Process()
    monkeypatch.setattr(r, '_launch', lambda *args: process)
    monkeypatch.setattr(r, '_collect', lambda *args: (status, {'stage': status}))
    result = r.run_job(job)
    assert result['status'] == status
    assert process.terminated and result['cleanup']['exited']
    assert result['capture_path'] is None


def test_export_validation_failure_cleans_owned_process(job, monkeypatch):
    process = Process()
    job['selector'] = 'a0_nonzero'
    monkeypatch.setattr(r, '_launch', lambda *args: process)
    monkeypatch.setattr(r, '_collect', lambda proc, out, deadline: fake_capture(job, out, a0=0))
    result = r.run_job(job)
    assert result['status'] == 'error' and 'nonzero selector' in result['error']
    assert process.terminated
    assert not (Path(job['output_dir']) / 'capture.json').exists()


@pytest.mark.parametrize('mutation,expected', [('rom', 'ROM differs'), ('exe', 'asset differs'),
    ('template', 'template differs'), ('manifest', 'manifest'), ('timeout', 'timeout'),
    ('selector', 'selector'), ('registers', 'register widths'), ('ram', '512 KiB')])
def test_changed_identity_and_invalid_job_decline_before_launch(job, monkeypatch, mutation, expected):
    if mutation == 'rom': Path(job['rom_path']).write_bytes(b'changed')
    if mutation == 'exe': (Path(job['portable_dir']) / 'Project64.exe').write_bytes(b'changed')
    if mutation == 'template': Path(r.__file__).with_name('project64_capture_entry.js').write_bytes(b'changed')
    if mutation == 'manifest': Path(r.__file__).with_name('project64_assets.json').write_bytes(b'{}')
    if mutation == 'timeout': job['timeout_seconds'] = 3600
    if mutation == 'selector': job['selector'] = 'injected javascript'
    if mutation == 'registers': job['plan']['registers']['a0']['bytes'] = 4
    if mutation == 'ram': job['plan']['ram'][0]['size'] = 1024 * 1024
    monkeypatch.setattr(r, '_launch', lambda *args: pytest.fail('must not launch'))
    result = r.run_job(job)
    assert result['status'] == 'error' and expected in result['error']
    assert result['cleanup']['owned_pid'] is None


def test_collect_honors_deadline_without_waiting(job, monkeypatch):
    monkeypatch.setattr(r.time, 'monotonic', lambda: 10)
    assert r._collect(Process(), Path(job['output_dir']), 9) == ('timeout', None)


def test_cleanup_error_never_returns_success(job, monkeypatch):
    process = Process()
    def fail_termination(): raise OSError('fixture handle failure')
    process.terminate = fail_termination
    monkeypatch.setattr(r, '_launch', lambda *args: process)
    monkeypatch.setattr(r, '_collect', lambda proc, out, deadline: fake_capture(job, out))
    result = r.run_job(job)
    assert result['status'] == 'cleanup_error'
    assert not result['cleanup']['exited']

"""Runtime-only contracts, independent of undeployed investigation features."""
import copy
import hashlib
import json
import shutil

import pytest

from eval import agentrepair, completion_campaign
from solver import runtime_capture as runtime, workspace
from solver import mips_differential as d


def capture_fixture(tmp_path):
    code = bytes.fromhex('2482000103e0000800000000')
    rom = tmp_path / 'rom.bin'
    rom.write_bytes(code)
    registers = {k: 0 for k in d._seed_registers(d.TestCase('x', 0))}
    registers.update(pc=0x80001000, a0=9, sp=0x80003000, ra=0x80004000, hi=0, lo=0)
    plan = {'function': 'f', 'architecture': 'mips-o32-be', 'byte_order': 'big',
            'entry': 0x80001000, 'code_size': len(code), 'rom_offset': 0,
            'rom_sha256': hashlib.sha256(code).hexdigest(),
            'registers': {name: {'number': i, 'bytes': 4} for i, name in enumerate(registers)},
            'ram': [{'name': 'stack', 'address': 0x80002000, 'size': 8192, 'kind': 'stack'}]}
    class Remote:
        def request(self, command):
            if command == '?':
                return 'S05'
            number = int(command[1:], 16)
            name = next(n for n, spec in plan['registers'].items() if spec['number'] == number)
            return registers[name].to_bytes(4, 'big').hex()
        def memory(self, address, size):
            return code if address == plan['entry'] else bytes(size)
    record = runtime.capture(Remote(), plan, rom)
    assembly = ('/* 000000 80001000 24820001 */ addiu v0,a0,1\n'
                '/* 000004 80001004 03E00008 */ jr ra\n'
                '/* 000008 80001008 00000000 */ nop\n')
    return rom, record, assembly


def test_capture_integrity_missing_registers_and_mmio(tmp_path):
    rom, record, _ = capture_fixture(tmp_path)
    runtime.verify(record, rom)
    record['registers']['a0'] = 1
    with pytest.raises(ValueError, match='checksum'):
        runtime.verify(record, rom)
    plan = copy.deepcopy(record['plan'])
    plan['ram'][0]['address'] = 0xA4600000
    with pytest.raises(ValueError, match='RDRAM'):
        runtime.validate_plan(plan)
    plan = copy.deepcopy(record['plan'])
    del plan['registers']['a0']
    with pytest.raises(ValueError, match='omits'):
        runtime.validate_plan(plan)


@pytest.mark.skipif(not all(shutil.which(n) for n in ('mips-linux-gnu-as', 'mips-linux-gnu-ld', 'mips-linux-gnu-objcopy')), reason='MIPS tools required')
def test_capture_reassembles_target_and_rejects_wrong_candidate(tmp_path):
    rom, record, assembly = capture_fixture(tmp_path)
    (tmp_path / 'snowboardkids.yaml').write_text(json.dumps({'sha1': hashlib.sha1(rom.read_bytes()).hexdigest(),
        'options': {'target_path': 'rom.bin'}, 'segments': [{'type': 'code', 'start': 0, 'vram': 0x80001000}]}))
    (tmp_path / 'symbol_addrs.txt').write_text('f = 0x80001000;\n')
    assert runtime.replay(record, rom, assembly, 'addiu v0,a0,1\njr ra\nnop', repo=tmp_path)['comparison']['status'] == 'passed'
    assert runtime.replay(record, rom, assembly, 'addiu v0,a0,2\njr ra\nnop', repo=tmp_path)['comparison']['status'] == 'failed'
    with pytest.raises(ValueError, match='text differs'):
        runtime.replay(record, rom, assembly.replace('a0,1', 'a0,2'), assembly, repo=tmp_path)


def test_remote_protocol_checks_checksum_and_partial_reads():
    class Sock:
        def __init__(self, data): self.data, self.sent = bytearray(data), []
        def recv(self, size):
            data = bytes(self.data[:size]); del self.data[:size]; return data
        def sendall(self, value): self.sent.append(value)
    sock = Sock(b'+$S05#b8')
    assert runtime.Remote(sock).request('?') == 'S05'
    assert sock.sent == [b'$?#3f', b'+']
    with pytest.raises(ValueError, match='checksum'):
        runtime.Remote(Sock(b'+$S05#00')).request('?')
    with pytest.raises(ValueError, match='partial'):
        runtime.Remote(Sock(b'+$00#60')).memory(0x80000000, 4)


@pytest.mark.parametrize('base_status,capture_status,expected', [
    ('observed_pass', 'failed', 'observed_failure'),
    ('observed_failure', 'passed', 'observed_failure'),
    ('unavailable', 'passed', 'observed_pass_with_execution_debt'),
    ('observed_pass', 'inconclusive', 'inconclusive'),
])
def test_runtime_panel_preserves_failures_and_debt(tmp_path, monkeypatch, base_status, capture_status, expected):
    from eval.captured_panel import Panel
    from solver.modelrepair import CandidateState
    (tmp_path / 'snowboardkids.yaml').write_text(json.dumps({'options': {'target_path': 'rom.bin'}}))
    (tmp_path / 'c_object_dump_normalized.s').write_text('jr ra\nnop')
    monkeypatch.setattr(workspace, 'semantic_assembly', lambda text, obj: text)
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'jr ra\nnop')
    monkeypatch.setattr(runtime, 'replay', lambda *a, **kw: {'comparison': {'status': capture_status}})
    class Base:
        report = {}
        def __call__(self, state): return {'status': base_status, 'counts': {}, 'semantic_key': [1], 'panel_sha256': 'base'}
    panel = Panel(Base(), tmp_path, tmp_path, 'f', [{'sha256': 'capture', 'plan': {'function': 'f'}}])
    state = CandidateState('int f(void){return 0;}', workspace.Attempt(True, 99, False, '', '', ''), tmp_path / 'c.o')
    result = panel(state)
    assert result['status'] == expected and result['counts'][capture_status] == 1
    assert result['panel_sha256'] != 'base' and result['authoritative'] is False


def test_completion_forwards_only_selected_function_captures(tmp_path, monkeypatch):
    source = tmp_path/'selected.c'
    source.write_text('int f(void){return 0;}')
    seen = []
    def run(**kwargs):
        seen.append(kwargs['runtime_captures'])
        return {'result': {'best_attempt_id': 4, 'best_source_path': str(source),
                           'best_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                           'best_residual': {'weighted_progress_score': 100}}}
    monkeypatch.setattr(completion_campaign.agentrepair, 'run', run)
    completion_campaign.execute(repo=tmp_path, db=tmp_path/'db', function='f',
        node={'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'attempt_id': 3},
        profile={'name': 'semantic_validate', 'model': False, 'deterministic_budget': 0},
        config={'model': 'unused', 'endpoint': 'unused', 'model_calls': 0, 'timeout': 1, 'num_predict': 10,
                'runtime_captures': {'f': [{'sha256': 'selected'}], 'g': [{'sha256': 'other'}]}},
        out=tmp_path/'result.json')
    assert seen == [({'sha256': 'selected'},)]


def test_agentrepair_passes_captured_panel_to_search(tmp_path, monkeypatch):
    from test_agentrepair import _database
    from eval import captured_panel, semantic_lane
    from solver import modelrepair
    db = tmp_path/'db.sqlite'
    _database(db).close()
    ws = tmp_path/'ws'
    ws.mkdir()
    monkeypatch.setattr(workspace, 'bootstrap', lambda *a: ws)
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel f\njr ra\nnop')
    monkeypatch.setattr(workspace, 'score', lambda *a, **kw: workspace.Attempt(False, 0, False, '', '', '', 71))
    captures = ({'sha256': 'captured'},)
    panels = []
    class Wrapped:
        def __init__(self, base, repo, ws, function, records):
            assert records == captures
            self.report = {'runtime_captures': ['captured']}
            panels.append(self)
        def __call__(self, state): return None
    monkeypatch.setattr(captured_panel, 'Panel', Wrapped)
    monkeypatch.setattr(semantic_lane, 'Panel', lambda *a: None)
    original = modelrepair.search
    def search(*a, **kw):
        assert kw['semantic_evaluator'] is panels[0]
        return original(*a, **kw)
    monkeypatch.setattr(modelrepair, 'search', search)
    result = agentrepair.run(repo=tmp_path, db=db, function='f', source='int f(void){return 0;}',
        source_parent_attempt_id=None, out=tmp_path/'result.json', best_source_out=tmp_path/'best.c',
        model='unused', endpoint='unused', draws=1, depth=1, beam=1, max_calls=0, timeout=1, think='low',
        num_thread=1, temperature=0, num_predict=10, seed=1, cache_dir=None, verbose=False,
        resilient=True, runtime_captures=captures)
    assert result['config']['runtime_captures'] == ['captured']
    assert result['semantic_panel']['runtime_captures'] == ['captured']

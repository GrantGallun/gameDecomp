import copy
import hashlib
import json
import shutil
import sqlite3

import pytest

from eval import capability_repair, completion_campaign as campaign
from solver import evidence_schedule, investigation, repair_queue, runtime_capture as runtime
from solver import shared_hypotheses, toolagent, workspace
from solver import mips_differential as d


def node(**kw):
    return {'status': 'pending', 'source_sha256': 'source', 'jobs': [],
            'instruction_count': 5, 'dag_level': 0,
            'residual': {'compiled': False, 'frontend': {'passed': False}}, **kw}


def test_campaign_routes_investigation_after_recovery_and_caps_repeated_hypotheses():
    n = node()
    state = {'config': {'scheduler': 'investigation-v1', 'model_calls': 3}, 'nodes': {'f': n}}
    name, first = campaign.choose(state)
    assert first['name'] == 'compile_recovery'
    campaign.accept(n, first, {'residual': n['residual']}, __import__('pathlib').Path('r'))
    assert campaign.choose(state)[1]['name'] == 'investigate'
    n['jobs'].extend([{'profile': 'investigate', 'evidence_key': str(i)} for i in range(3)])
    assert campaign.choose(state)[1]['name'] != 'investigate'


def evidence_db():
    conn = sqlite3.connect(':memory:')
    conn.executescript('CREATE TABLE functions(name,addr); CREATE TABLE evidence(id,func_addr,base,offset,width);'
                       "INSERT INTO functions VALUES('a',1),('b',2),('c',3);"
                       "INSERT INTO evidence VALUES(1,1,'global:0x800000AB',0,4),"
                       "(2,2,'global:0x800000ab',4,2),(3,3,'param0',0,4);")
    return conn


def test_shared_hypothesis_consumers_retraction_and_no_inference_as_evidence(tmp_path):
    with evidence_db() as conn:
        tools = investigation.Tools(tmp_path, tmp_path, conn, 'a', tmp_path)
        observed = json.loads(tools.evidence('a'))
        action = toolagent.parse_action(json.dumps({'action': 'record_hypothesis',
            'subject': 'global:0x800000ab', 'alternatives': ['record pointer', 'byte buffer'],
            'support': [observed['id']]}))
        tools.remember(action)
        state = {'nodes': {n: node() for n in ('a', 'b', 'c')}}
        shared_hypotheses.ingest(state, 'a', {'observations': tools.receipts, 'hypotheses': tools.hypotheses}, conn)
        assert state['nodes']['b']['shared_context']['hypotheses']
        assert not state['nodes']['c']['shared_context']['hypotheses']
        before = repair_queue.evidence_key(state['nodes']['b'])
        affected = shared_hypotheses.retract(state['shared_hypotheses'], observed['id'], 'wrong extraction revision')
        assert affected == ['a', 'b']
        shared_hypotheses.ingest(state, 'a', {}, conn)
        assert repair_queue.evidence_key(state['nodes']['b']) != before
        assert not state['nodes']['b']['shared_context']['hypotheses']
        assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == 3
        with pytest.raises(ValueError, match='unknown observations'):
            shared_hypotheses.add({}, subject='x', alternatives=['a', 'b'], support=['fake'], consumers=['a'], origin='a')


def test_compiler_probe_rejects_preprocessor_before_compiling(tmp_path):
    with evidence_db() as conn:
        tools = investigation.Tools(tmp_path, tmp_path, conn, 'a', tmp_path)
        with pytest.raises(ValueError, match='self-contained'):
            tools.probe('#include "/secret"\nint f(void) {return 0;}', 'prediction')
        with pytest.raises(ValueError, match='TU identity'):
            tools.probe('int f(void) {return 0;}', 'prediction')


def test_capability_jobs_are_shared_once_and_never_promote_source(tmp_path):
    nodes = {n: node(status='parked', blocker={'status': 'object_postprocessing_backend_required',
             'evidence': {'target': 'build/src/f.o', 'postprocess': 'trim', 'makefile_sha256': 'm'}}) for n in ('a', 'b')}
    key, issue = next(iter(repair_queue.shared_issues(nodes).items()))
    task = {'issue_key': key, 'evidence': issue['identity'], 'modules': ['solver/repair.py']}
    state = {'config': {'scheduler': 'investigation-v1', 'model_calls': 2, 'capability_tasks': {key: task}}, 'nodes': nodes}
    name, profile = campaign.choose(state)
    assert profile['name'] == 'capability_repair'
    old = copy.deepcopy(nodes[name])
    campaign.accept(nodes[name], profile, {'auxiliary': True, 'status': 'validated_candidate_requires_frozen_fork'}, tmp_path / 'r')
    assert nodes[name]['status'] == old['status'] and nodes[name]['source_sha256'] == old['source_sha256']
    assert campaign.choose(state) is None


def test_engineering_patch_is_atomic_scoped_and_syntax_checked(tmp_path):
    (tmp_path / 'solver').mkdir()
    module = tmp_path / 'solver' / 'x.py'
    module.write_text('VALUE = 1\n')
    with pytest.raises(ValueError):
        capability_repair.apply(tmp_path, {'edits': [{'path': 'solver/x.py', 'old': '1', 'new': '2'},
            {'path': 'solver/workspace.py', 'old': 'x', 'new': 'y'}]}, {'solver/x.py'})
    assert module.read_text() == 'VALUE = 1\n'
    with pytest.raises(SyntaxError):
        capability_repair.apply(tmp_path, {'edits': [{'path': 'solver/x.py', 'old': '1', 'new': '('}]}, {'solver/x.py'})
    assert module.read_text() == 'VALUE = 1\n'
    capability_repair.apply(tmp_path, {'edits': [{'path': 'solver/x.py', 'old': '1', 'new': '2'}]}, {'solver/x.py'})
    assert module.read_text() == 'VALUE = 2\n'


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


def test_capture_integrity_missing_registers_mmio_and_target_identity(tmp_path):
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
def test_capture_replay_reassembles_target_and_detects_wrong_candidate(tmp_path):
    rom, record, assembly = capture_fixture(tmp_path)
    (tmp_path / 'snowboardkids.yaml').write_text(json.dumps({'sha1': hashlib.sha1(rom.read_bytes()).hexdigest(),
        'options': {'target_path': 'rom.bin'}, 'segments': [{'type': 'code', 'start': 0, 'vram': 0x80001000}]}))
    (tmp_path / 'symbol_addrs.txt').write_text('f = 0x80001000;\n')
    result = runtime.replay(record, rom, assembly, 'addiu v0,a0,1\njr ra\nnop', repo=tmp_path)
    assert result['comparison']['status'] == 'passed', result
    wrong = runtime.replay(record, rom, assembly, 'addiu v0,a0,2\njr ra\nnop', repo=tmp_path)
    assert wrong['comparison']['status'] == 'failed'
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

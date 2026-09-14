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


def test_engineering_worker_reproduces_repairs_and_tests_transfer_in_private_copy(tmp_path):
    project = tmp_path / 'project'
    (project / 'solver').mkdir(parents=True)
    (project / 'solver/__init__.py').write_text('')
    module = project / 'solver/demo.py'
    module.write_text('def successor(x):\n    return x\n')
    (project / 'tests').mkdir()
    (project / 'tests/test_demo.py').write_text('from solver.demo import successor\n'
        'def test_reproduce(): assert successor(0) == 1\n'
        'def test_regression(): assert isinstance(successor(2), int)\n'
        'def test_transfer(): assert successor(8) == 9\n')
    task = {'issue_key': 'issue', 'evidence': {'failure': 'wrong successor'},
            'modules': ['solver/demo.py'], 'reproduce': ['tests/test_demo.py::test_reproduce'],
            'regression': ['tests/test_demo.py::test_regression'], 'transfer': ['tests/test_demo.py::test_transfer']}
    class Provider:
        def generate(self, request):
            assert 'wrong successor' in request.prompt
            return json.dumps({'hypothesis': 'missing increment', 'edits': [
                {'path': 'solver/demo.py', 'old': 'return x', 'new': 'return x + 1'}]}), {'eval_count': 12}
    report = capability_repair.run(project, task, tmp_path / 'experiment', calls=1, provider=Provider(), endpoint='unused')
    assert report['status'] == 'validated_candidate_requires_frozen_fork', report
    assert report['baseline']['returncode'] == 1
    assert report['attempts'][0]['transfer']['passed']
    assert module.read_text() == 'def successor(x):\n    return x\n'


def test_tool_agent_observes_before_patch_and_retains_behavioral_candidates(tmp_path, monkeypatch):
    source = 'int a(void) { return 0; }'
    base = workspace.Attempt(True, 90, False, '-li v0,1\n+li v0,0', '', '', frontend={'passed': True})
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'jr ra\nli v0,1')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    monkeypatch.setattr(workspace, 'score', lambda *a, **k: workspace.Attempt(True, 95, False, '', '', '', frontend={'passed': True}))
    class Provider:
        provider_id = 'scripted-investigation'
        def __init__(self):
            self.responses = iter([json.dumps({'action': 'inspect_evidence', 'query': 'a'}),
                json.dumps({'action': 'patch', 'kind': 'other', 'hypothesis': 'constant differs',
                            'edits': [{'old': 'return 0;', 'new': 'return 1;'}]})])
        def generate(self, request): return next(self.responses), {'eval_count': 10}
    with evidence_db() as conn:
        tools = investigation.Tools(tmp_path, tmp_path, conn, 'a', tmp_path)
        result = toolagent.search(tmp_path, 'a', source, tmp_path, model='scripted', endpoint='unused',
            base_attempt=base, provider=Provider(), max_calls=2, investigation_tools=tools.handlers(),
            semantic_evaluator=lambda state: {'status': 'observed_pass' if 'return 1;' in state.source else 'observed_failure'})
    assert result.calls_attempted == 2 and result.tool_actions == 1 and result.compiles == 1
    assert result.best.source == 'int a(void) { return 1; }'
    assert result.candidates[-1].semantic['status'] == 'observed_pass'
    assert tools.receipts[0]['observations'][0]['id'] == 1


@pytest.mark.parametrize('base_status,capture_status,expected', [
    ('observed_pass', 'failed', 'observed_failure'),
    ('observed_failure', 'passed', 'observed_failure'),
    ('unavailable', 'passed', 'observed_pass_with_execution_debt'),
    ('observed_pass', 'inconclusive', 'inconclusive'),
])
def test_runtime_panel_never_hides_failures_or_execution_debt(tmp_path, monkeypatch, base_status, capture_status, expected):
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
    assert result['status'] == expected
    assert result['counts'][capture_status] == 1
    assert result['panel_sha256'] != 'base' and result['authoritative'] is False


def test_shared_claim_handles_call_observations_without_memory_base(tmp_path):
    with evidence_db() as conn:
        conn.execute("INSERT INTO evidence VALUES(4,1,NULL,0,4)")
        tools = investigation.Tools(tmp_path, tmp_path, conn, 'a', tmp_path)
        receipt = json.loads(tools.evidence('a'))
        state = {'nodes': {'a': node(), 'b': node()}}
        payload = {'observations': tools.receipts, 'hypotheses': [{'subject': 'global:0x800000ab',
                   'alternatives': ['array', 'record'], 'support': [receipt['id']]}]}
        shared_hypotheses.ingest(state, 'a', payload, conn)
        assert state['nodes']['b']['shared_context']['hypotheses']


def test_investigation_cannot_finish_after_only_missing_header_lookup(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'jr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    class Provider:
        provider_id = 'scripted'
        def __init__(self): self.actions = iter([
            {'action': 'inspect_definition', 'query': 'Missing'},
            {'action': 'finish', 'reason': 'header missing'}])
        def generate(self, request): return json.dumps(next(self.actions)), {'eval_count': 5}
    base = workspace.Attempt(True, 90, False, '', '', '')
    result = toolagent.search(tmp_path, 'a', 'int a(void){return 0;}', tmp_path,
        model='fake', endpoint='unused', base_attempt=base, provider=Provider(), max_calls=2,
        investigation_tools={'inspect_evidence': lambda action: 'observations'})
    assert result.events[-1]['status'] == 'curiosity-budget-unmet'
    assert 'target evidence' in result.events[-1]['error']

"""Real isolation tests plus deterministic coordinator failure/coverage tests."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def sandbox():
    assert importlib.util.find_spec('eval.tool_sandbox'), 'isolated tool runner is missing'
    from eval.tool_sandbox import run_tool
    return run_tool


linux = pytest.mark.skipif(sys.platform != 'linux', reason='real Linux namespaces required')


@linux
def test_tool_generates_candidate_without_host_access(tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('evaluation answer')
    code = '''import json, os, socket, sys
x = json.load(sys.stdin)
assert not os.path.exists(%r)
assert not os.path.exists('/mnt/c/Code/gameDecomp')
assert not os.path.exists('/home/grant')
assert 'TOOL_LEARNING_SECRET' not in os.environ
try:
    socket.create_connection(('127.0.0.1', 11434), timeout=.1)
except OSError:
    pass
else:
    raise AssertionError('network available')
print(json.dumps({'candidates': [x['source'].replace('>>', '<<')]}))
''' % str(secret)
    result = sandbox()(code, {'source': 'int f(int x) {return x >> 2;}'})
    assert result['status'] == 'ok', result
    assert result['candidates'] == ['int f(int x) {return x << 2;}']
    assert secret.read_text() == 'evaluation answer'


@linux
@pytest.mark.parametrize('code', [
    'import json; print(json.dumps({"candidates": [], "exact": True}))',
    'print("not json")',
    'raise RuntimeError("broken tool")',
    'print("x" * 1000000)',
    'while True: pass',
])
def test_bad_tool_returns_failure_and_next_tool_still_runs(code):
    run = sandbox()
    result = run(code, {'source': 'int f(void){return 1;}'}, timeout=1)
    assert result['status'] != 'ok'
    assert result['candidates'] == []
    assert run('print(\'{"candidates": []}\')', {})['status'] == 'ok'


@linux
def test_absent_sandbox_fails_closed(monkeypatch):
    run = sandbox()
    monkeypatch.setattr('eval.tool_sandbox.shutil.which', lambda _: None)
    assert run('raise AssertionError("must never run")', {})['status'] == 'unavailable'


@linux
def test_tool_cannot_multiply_resources_by_fork_exec_or_large_tmpfs():
    code = '''import os, json, errno
for path in ('/escape', '/dev/escape'):
    try:
        open(path, 'w').write('must fail')
    except OSError:
        pass
    else:
        raise AssertionError('unbounded writable mount: ' + path)
for action in (lambda: os.fork(), lambda: os.execv('/usr/bin/true', ['true']),
               lambda: os.memfd_create('memory')):
    try:
        action()
    except PermissionError:
        pass
    else:
        raise AssertionError('unbounded resource mechanism available')
for root in ('/tmp', '/work', '/dev/shm'):
    stat = os.statvfs(root)
    assert stat.f_blocks * stat.f_frsize <= 16 * 1024 * 1024
print(json.dumps({'candidates': []}))
'''
    result = sandbox()(code, {})
    assert result['status'] == 'ok', result


@pytest.mark.parametrize('source', [
    '#include "/etc/passwd"', '%:include "x"', '??=include "x"',
    'int f(){ __asm__(".incbin x"); }', 'int f(){return 0;}\\\n',
])
def test_candidate_c_cannot_read_files_or_inject_binary(source):
    assert importlib.util.find_spec('eval.tool_learning_oracle'), 'oracle is missing'
    from eval.tool_learning_oracle import validate_source
    with pytest.raises(ValueError):
        validate_source(source)


def test_panel_rejects_family_leakage_and_duplicate_ids():
    assert importlib.util.find_spec('eval.tool_learning'), 'coordinator is missing'
    from eval.tool_learning import validate_panel
    rows = [dict(id='a', family='shared', split='dev', source='x', target='a.o'),
            dict(id='b', family='shared', split='eval', source='y', target='b.o')]
    with pytest.raises(ValueError, match='famil'):
        validate_panel(rows)
    rows[1].update(id='a', family='separate')
    with pytest.raises(ValueError, match='duplicate'):
        validate_panel(rows)


def test_gate_rejects_lost_coverage_unknown_and_unpaid_development():
    assert importlib.util.find_spec('eval.tool_learning'), 'coordinator is missing'
    from eval.tool_learning import decide
    before = [dict(id='a', exact=True, status='complete', compiles=2, seconds=1),
              dict(id='b', exact=False, status='complete', compiles=4, seconds=1)]
    after = [dict(id='a', exact=False, status='complete', compiles=2, seconds=1),
             dict(id='b', exact=True, status='complete', compiles=2, seconds=1)]
    assert not decide(before, after, development={'compiles': 0, 'seconds': 0},
                      caps={'compiles': 10, 'seconds': 10}, motivating=True)['retain']
    after[0]['exact'] = True
    after[1]['status'] = 'infra_error'
    assert not decide(before, after, development={'compiles': 0, 'seconds': 0},
                      caps={'compiles': 10, 'seconds': 10}, motivating=True)['retain']
    after[1]['status'] = 'complete'
    result = decide(before, after, development={'compiles': 7, 'seconds': 0},
                    caps={'compiles': 10, 'seconds': 10}, motivating=True)
    assert not result['retain'] and 'budget' in result['reason']


def test_gate_accepts_added_coverage_within_total_budget():
    assert importlib.util.find_spec('eval.tool_learning'), 'coordinator is missing'
    from eval.tool_learning import decide
    a = [dict(id='a', exact=False, status='complete', compiles=4, seconds=2)]
    b = [dict(id='a', exact=True, status='complete', compiles=2, seconds=1)]
    assert decide(a, b, development={'compiles': 2, 'seconds': 1},
                  caps={'compiles': 4, 'seconds': 4}, motivating=True)['retain']


class FakeOracle:
    """Coordinator tests only. Integration tests below use the actual compiler."""
    def check_integrity(self):
        pass

    def score(self, case, source, *, deadline=None):
        import hashlib
        exact = 'return 2' in source
        return dict(status='ok', exact=exact, compiled=True, compiles=1, seconds=.01,
                    source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                    diff='- return 2\n+ return 1', target_asm='target assembly',
                    certificate={'exact': exact, 'status': 'object_sections_exact' if exact else 'object_sections_differ'})


def cases(tmp_path):
    result = []
    for name, split in [('d1', 'dev'), ('d2', 'dev'), ('e1', 'eval'), ('e2', 'eval')]:
        target = tmp_path / (name + '.o')
        target.write_bytes(name.encode())
        result.append(dict(id=name, family=split, split=split,
                           source='int '+name+'(void){return 1;}', target=str(target)))
    return result


@linux
def test_loop_freezes_tool_and_exports_only_development(tmp_path):
    assert importlib.util.find_spec('eval.tool_learning'), 'coordinator is missing'
    from eval.tool_learning import Limits, run_experiment
    seen = []
    code = '''import json, sys
x = json.load(sys.stdin)
print(json.dumps({'candidates': [x['source'].replace('return 1', 'return 2')]}))
'''
    def proposer(messages):
        seen.append(json.dumps(messages))
        return {'name': 'repair', 'rationale': 'test', 'code': code}, {'tokens': 25, 'seconds': .1}
    out = tmp_path / 'run'
    report = run_experiment(cases(tmp_path), proposer, FakeOracle(), out,
                            limits=Limits(compiles=12, seconds=20), baseline=lambda _: [])
    assert len(seen) == 1 and 'e1' not in seen[0] and 'e2' not in seen[0]
    assert report['evaluation']['covered_after'] == 2
    assert report['decision']['retain']
    training = [json.loads(s) for s in (out / 'development-sft.jsonl').read_text().splitlines()]
    assert len(training) == 2
    assert {r['function'] for r in training} == {'d1', 'd2'}
    assert all(r['split'] == 'train' for r in training)
    assert (out / 'library' / 'tool.json').is_file()
    ledger = [json.loads(s) for s in (out / 'events.jsonl').read_text().splitlines()]
    assert sum(r['kind'] == 'compile' for r in ledger) == report['all_compiles']
    assert all(r['source_sha256'] == r['receipt']['source_sha256'] for r in ledger if r['kind'] == 'compile')


@linux
def test_author_can_repair_tool_using_only_development_feedback(tmp_path):
    from eval.tool_learning import Limits, run_experiment
    prompts = []
    def proposer(messages):
        prompts.append(json.dumps(messages))
        code = ('raise RuntimeError("broken")' if len(prompts) == 1 else
                "import json,sys; x=json.load(sys.stdin); print(json.dumps({'candidates': [x['source'].replace('return 1','return 2')]}))")
        return {'name': 'fix', 'code': code, 'rationale': 'test'}, {'tokens': 10, 'seconds': .1}
    report = run_experiment(cases(tmp_path), proposer, FakeOracle(), tmp_path / 'run',
                            limits=Limits(compiles=12, seconds=20), baseline=lambda _: [])
    assert len(prompts) == 2
    assert 'broken' in prompts[1] and 'e1(' not in prompts[1] and 'e2(' not in prompts[1]
    assert report['development']['tokens'] == 20
    assert report['decision']['retain']


@linux
def test_broken_tool_does_not_drop_other_cases_or_baseline(tmp_path):
    assert importlib.util.find_spec('eval.tool_learning'), 'coordinator is missing'
    from eval.tool_learning import Limits, run_experiment
    def proposer(messages):
        return {'name': 'broken', 'rationale': 'test', 'code': 'raise RuntimeError()'}, {'tokens': 1, 'seconds': .1}
    report = run_experiment(cases(tmp_path), proposer, FakeOracle(), tmp_path / 'run',
                  limits=Limits(compiles=6, seconds=15),
                  baseline=lambda obs: [obs['source'].replace('return 1', 'return 2')])
    assert report['evaluation']['covered_before'] == report['evaluation']['covered_after'] == 2
    assert not report['decision']['retain']
    assert len(report['after']) == 2


@linux
def test_real_ido_oracle_confirms_bytes_and_detects_target_mutation(tmp_path):
    from eval.tool_learning_oracle import Oracle
    compiler = Path('/home/grant/decomp/sbk1/tools/ido-recomp/linux')
    if not compiler.is_dir():
        pytest.skip('local IDO not installed')
    # WSL compiler trees and scratch must be on the Linux filesystem.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='tool-oracle-test-') as directory:
        target = Path(directory) / 'target.o'
        maker = Oracle(compiler, [])
        source = 'int syn_probe(int x) { return x + 7; }'
        built = maker.compile(source, target)
        assert built['compiled'], built
        case = dict(id='real', target=str(target))
        oracle = Oracle(compiler, [case])
        same = oracle.score(case, source)
        assert same['exact'] and same['certificate']['exact'], same
        different = oracle.score(case, source.replace('+ 7', '+ 8'))
        assert different['compiled'] and not different['exact'], different
        rejected = oracle.score(case, 'int syn_probe( { return }')
        assert not rejected['exact'] and not rejected['compiled']
        target.write_bytes(b'changed')
        corrupt = oracle.score(case, source)
        assert corrupt['status'] == 'infra_error' and not corrupt['exact']


@linux
def test_final_integrity_failure_invalidates_coverage_and_training(tmp_path):
    from eval.tool_learning import Limits, run_experiment
    class ChangedOracle(FakeOracle):
        def check_integrity(self):
            raise RuntimeError('compiler changed')
    def proposer(messages):
        return {'name': 'fix', 'rationale': 'test', 'code':
                "import json,sys; x=json.load(sys.stdin); print(json.dumps({'candidates':[x['source'].replace('return 1','return 2')]}))"}, {'tokens': 1}
    out = tmp_path / 'run'
    report = run_experiment(cases(tmp_path), proposer, ChangedOracle(), out,
                            limits=Limits(compiles=12, seconds=20), baseline=lambda _: [])
    assert not report['decision']['retain']
    assert report['coverage_after']['covered'] == 0
    assert all(r['status'] == 'quarantined' and not r['exact'] for r in report['after'])
    assert not (out / 'development-sft.jsonl').read_text()


def test_one_absolute_deadline_reaches_every_case_compiler_call(tmp_path):
    from eval.tool_learning import Journal, Limits, run_case
    import time
    seen = []
    class DeadlineOracle(FakeOracle):
        def score(self, case, source, *, deadline=None):
            assert deadline is not None
            seen.append(deadline)
            return super().score(case, source, deadline=deadline)
    start = time.monotonic()
    row = run_case(cases(tmp_path)[0], DeadlineOracle(), Journal(tmp_path / 'events'),
                   arm='baseline', limits=Limits(seconds=2),
                   baseline=lambda obs: [obs['source'].replace('return 1', 'return 2')])
    assert row['exact']
    assert len(seen) == 3 and len(set(seen)) == 1
    assert start + 2 <= seen[0] <= start + 2.1


@linux
def test_malformed_author_output_still_charges_all_tokens(tmp_path):
    from eval.tool_learning import Limits, run_experiment
    count = 0
    def proposer(messages):
        nonlocal count
        count += 1
        if count == 1:
            return '{broken json', {'tokens': 26}
        return json.dumps({'name': 'fix', 'rationale': 'test', 'code':
                "import json,sys; x=json.load(sys.stdin); print(json.dumps({'candidates':[x['source'].replace('return 1','return 2')]}))"}), {'tokens': 30}
    report = run_experiment(cases(tmp_path), proposer, FakeOracle(), tmp_path / 'run',
                            limits=Limits(compiles=12, seconds=20), baseline=lambda _: [])
    assert count == 2
    assert report['development']['tokens'] == 56
    assert report['decision']['retain']


@linux
def test_actual_ido_loop_can_retain_a_working_control_and_export_receipts():
    """Developer-authored integration control, NOT evidence of model capability."""
    from eval.tool_learning import Limits, run_experiment
    from eval.tool_learning_oracle import Oracle
    import tempfile
    compiler = Path('/home/grant/decomp/sbk1/tools/ido-recomp/linux')
    if not compiler.is_dir():
        pytest.skip('local IDO not installed')
    with tempfile.TemporaryDirectory(prefix='tool-loop-integration-') as directory:
        root = Path(directory)
        maker = Oracle(compiler, [])
        panel = []
        for number in range(5):
            split = 'dev' if number < 2 else 'eval'
            source = f'int syn_case_{number}(int value) {{return value >> {number + 2};}}'
            target = root / f'{number}.o'
            assert maker.compile(source.replace('(int ', '(unsigned int ', 1), target)['compiled']
            panel.append(dict(id=str(number), family=split, split=split, source=source, target=str(target)))
        def control(messages):
            return {'name': 'unsigned_shift', 'rationale': 'developer-authored positive control',
                    'code': "import json,sys; x=json.load(sys.stdin); print(json.dumps({'candidates': [x['source'].replace('(int ', '(unsigned int ', 1)] if 'srl' in x['target_asm'] and 'sra' in x['candidate_asm'] else []}))"}, {'tokens': 0}
        out = root / 'run'
        result = run_experiment(panel, control, Oracle(compiler, panel), out,
                                limits=Limits(compiles=12), baseline=lambda _: [])
        assert result['decision']['retain'], result
        assert result['coverage_after']['covered'] == 3
        assert len((out / 'development-sft.jsonl').read_text().splitlines()) == 2
        for row in map(json.loads, (out / 'events.jsonl').read_text().splitlines()):
            if row['kind'] == 'compile' and row['receipt']['exact']:
                assert row['receipt']['certificate']['status'] == 'object_sections_exact'


@linux
def test_one_failed_case_does_not_hide_or_stop_other_evaluation_cases(tmp_path):
    from eval.tool_learning import Limits, run_experiment
    class BrokenCaseOracle(FakeOracle):
        def score(self, case, source, *, deadline=None):
            if case['id'] == 'e1':
                raise RuntimeError('one broken compiler route')
            return super().score(case, source, deadline=deadline)
    def proposer(messages):
        return {'name': 'fix', 'rationale': 'test', 'code':
                "import json,sys; x=json.load(sys.stdin); print(json.dumps({'candidates':[x['source'].replace('return 1','return 2')]}))"}, {'tokens': 1}
    report = run_experiment(cases(tmp_path), proposer, BrokenCaseOracle(), tmp_path / 'run',
                            limits=Limits(compiles=12, seconds=20), baseline=lambda _: [])
    assert not report['decision']['retain']
    assert report['coverage_after']['n'] == 2
    assert report['coverage_after']['covered'] == 1
    assert report['coverage_after']['missing_rows'] == []
    rows = {r['id']: r for r in report['after']}
    assert rows['e1']['status'] == 'infra_error'
    assert rows['e2']['exact']


def test_author_message_states_the_one_object_contract():
    # 2026-10-03: both 14B authors iterated `for item in data` because they were shown a list but given one object
    from eval.tool_learning import AUTHOR_PROMPT, author_message
    assert "ONE observation object" in AUTHOR_PROMPT and "def propose(obs)" in AUTHOR_PROMPT
    assert "ONE of these objects" in author_message([{"source": "int f(void){return 0;}"}])


def test_development_feedback_reports_each_candidate_and_an_empty_tool():
    from eval.tool_learning import development_feedback
    rows = [{"id": "dev0", "exact": False, "tool_status": "ok",
             "tool_feedback": {"status": "ok", "candidate_count": 0}},
            {"id": "dev1", "exact": False, "tool_status": "ok",
             "tool_feedback": {"status": "ok", "candidate_count": 1, "candidates": [
                 {"source": "int f(int v){return v>>>3;}", "compiled": False, "exact": False,
                  "compiler_error": "Syntax Error"}]}}]
    fb = development_feedback(None, rows)["development"]
    assert "emitted no candidates" in fb[0]["note"]
    assert fb[1]["candidates"][0]["compiler_error"] == "Syntax Error"
    assert development_feedback("ValueError: bad json", [])["proposal_error"].startswith("ValueError")

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from eval import inline_regions as audit


def fixture(tmp_path, monkeypatch):
    run, repo = tmp_path / 'run', tmp_path / 'repo'
    run.mkdir()
    repo.mkdir()
    pointer = {'kind': audit.campaign_state.KIND, 'store': 'campaign.state.sqlite',
               'commit': 42, 'sha256': 'manifest'}
    (run / 'campaign.json').write_text(json.dumps(pointer))
    state = {'config': {'repo': str(repo)}, 'nodes': {}, 'pins': {}}
    for name, status in [('leaf', 'object_exact'), ('large', 'pending')]:
        path = repo / 'nonmatchings' / name / 'target.s'
        path.parent.mkdir(parents=True)
        path.write_text('.macro glabel name\n.endm\nglabel ' + name +
                        '\n/* 000040 80000400 00000000 */ nop\nendlabel ' + name + '\n')
        state['nodes'][name] = {'address': 0x80000400, 'status': status, 'size': 4}
        state['pins'][str(path)] = audit.digest(path.read_bytes())
    monkeypatch.setattr(audit.campaign_state, 'read', lambda path: copy.deepcopy(state))
    return run, repo, state


def test_collect_all_statuses_pins_raw_bytes_and_freezes_pointer(tmp_path, monkeypatch):
    run, repo, state = fixture(tmp_path, monkeypatch)
    original = (run / 'campaign.json').read_bytes()
    inputs = audit.collect(run, tmp_path / 'audit')
    assert set(inputs['assemblies']) == {'leaf', 'large'}
    assert inputs['checkpoint'] == 42 and inputs['unavailable'] == []
    assert not inputs['assemblies']['leaf'].startswith('.macro')
    saved = tmp_path / 'audit' / inputs['bindings']['leaf']['saved_path']
    assert saved.read_bytes() == (repo / 'nonmatchings/leaf/target.s').read_bytes()
    assert (run / 'campaign.json').read_bytes() == original
    (run / 'campaign.json').write_text('{}')
    assert json.loads((tmp_path / 'audit/checkpoint.json').read_bytes())['commit'] == 42


@pytest.mark.parametrize('fault,reason', [('tamper', 'differs from checkpoint pin'),
    ('unpinned', 'no checkpoint pin'), ('address', 'entry differs'),
    ('missing', 'No such file')])
def test_bad_targets_are_explicitly_excluded(tmp_path, monkeypatch, fault, reason):
    run, repo, state = fixture(tmp_path, monkeypatch)
    path = repo / 'nonmatchings/leaf/target.s'
    if fault == 'tamper':
        path.write_text(path.read_text() + '# changed')
    elif fault == 'unpinned':
        del state['pins'][str(path)]
    elif fault == 'address':
        state['nodes']['leaf']['address'] += 4
    else:
        path.unlink()
    inputs = audit.collect(run, tmp_path / 'audit')
    assert set(inputs['assemblies']) == {'large'}
    assert inputs['unavailable'][0]['function'] == 'leaf'
    assert reason in inputs['unavailable'][0]['reason']


def test_extractor_rejects_neighboring_or_wrong_function():
    for text in ('glabel wrong\nnop', 'glabel f\nnop\nglabel neighbor\nnop',
                 'glabel f\nnop\nendlabel f\nglabel f\nnop'):
        with pytest.raises(ValueError):
            audit.body(text.encode(), 'f')


def test_saved_replay_requires_untampered_raw_and_extracted_inventory(tmp_path, monkeypatch):
    run, _, _ = fixture(tmp_path, monkeypatch)
    audit.collect(run, tmp_path / 'audit')
    path = tmp_path / 'audit/assemblies.json'
    valid = json.loads(path.read_bytes())
    monkeypatch.setattr(audit.campaign_state, 'read', lambda _: pytest.fail('replay consulted live state'))
    assert audit.load_saved(path)['checkpoint'] == 42
    modified = copy.deepcopy(valid)
    modified['assemblies']['leaf'] += '\nnop'
    path.write_text(json.dumps(modified))
    with pytest.raises(ValueError, match='changed'):
        audit.load_saved(path)
    path.write_text(json.dumps(valid))
    target = path.parent / valid['bindings']['leaf']['saved_path']
    target.write_bytes(target.read_bytes() + b'# tamper')
    with pytest.raises(ValueError, match='changed'):
        audit.load_saved(path)


def test_saved_paths_cannot_escape_or_overwrite_input(tmp_path, monkeypatch):
    run, _, _ = fixture(tmp_path, monkeypatch)
    audit.collect(run, tmp_path / 'audit')
    path = tmp_path / 'audit/assemblies.json'
    valid = json.loads(path.read_bytes())
    for target in ('../target.s', str(tmp_path / 'absolute.s')):
        valid['bindings']['leaf']['saved_path'] = target
        path.write_text(json.dumps(valid))
        with pytest.raises(ValueError, match='confined'):
            audit.load_saved(path)


def test_cli_replay_uses_only_retained_assembly_and_records_engine_hash(tmp_path, monkeypatch, capsys):
    run, _, _ = fixture(tmp_path, monkeypatch)
    inputs = audit.collect(run, tmp_path / 'audit')
    calls = []
    def analyse(assemblies, **kwargs):
        calls.append((assemblies, kwargs))
        return {'regions': ['diagnostic']}
    engine = SimpleNamespace(analyse=analyse, __file__=__file__)
    import solver
    monkeypatch.setattr(solver, 'inline_regions', engine, raising=False)
    monkeypatch.setattr(audit.campaign_state, 'read', lambda _: pytest.fail('live read'))
    audit.main(['--assemblies', str(tmp_path / 'audit/assemblies.json'), '--out', str(tmp_path / 'replay'),
                '--min-instructions', '9', '--large-instructions', '150'])
    assert calls == [(inputs['assemblies'], {'min_instructions': 9, 'large_instructions': 150})]
    report = json.loads((tmp_path / 'replay/report.json').read_bytes())
    assert report['analysis'] == {'regions': ['diagnostic']}
    assert report['implementation_sha256']['solver/inline_regions.py'] == audit.digest(Path(__file__).read_bytes())
    assert json.loads(capsys.readouterr().out)['available_functions'] == 2
    assert audit.load_saved(tmp_path / 'replay/assemblies.json') == inputs


def test_existing_output_is_never_reused_and_bad_thresholds_fail_before_read(tmp_path, monkeypatch):
    run, _, _ = fixture(tmp_path, monkeypatch)
    out = tmp_path / 'existing'
    out.mkdir()
    with pytest.raises(FileExistsError):
        audit.collect(run, out)
    with pytest.raises(SystemExit):
        audit.main(['--run', str(run), '--out', str(tmp_path / 'bad'), '--min-instructions', '0'])
    assert not (tmp_path / 'bad').exists()

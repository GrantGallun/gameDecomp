import json
from eval import gamewide_batch as batch


def test_batch_advances_past_blocked_intake_then_dispatches_census(tmp_path, monkeypatch):
    calls = []
    def intake(**kwargs):
        calls.append('intake')
        if calls.count('intake') == 1:
            return {'status': 'complete', 'selection': [{'function': 'sdk'}],
                    'nodes': [{'function': 'sdk', 'status': 'assembly_backend_required'}]}
        path = kwargs['output'].with_name(kwargs['output'].stem + '.census.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'dag': {'nodes': [{'function': 'f'}]}}))
        return {'status': 'complete', 'selection': [{'function': 'f'}],
                'nodes': [{'function': 'f', 'status': 'compiled'}]}
    def repair(**kwargs):
        calls.append('repair')
        assert kwargs['functions'] == ('f',)
        assert kwargs['rounds'] == 2
        return {'status': 'complete', 'aggregate': {'exact': 0}}
    monkeypatch.setattr(batch.gamewide_probe, 'run', intake)
    monkeypatch.setattr(batch.frozen_wavefront, 'run', repair)
    receipt = batch.run(repo=tmp_path, db=tmp_path/'db', project=tmp_path,
                        output=tmp_path/'out.json', batches=2, rounds=2)
    assert calls == ['intake', 'intake', 'repair']
    assert receipt['status'] == 'complete'
    assert receipt['batches'][0]['intake_outcomes'][0]['status'] == 'assembly_backend_required'
    assert receipt['whole_rom_verified'] is False

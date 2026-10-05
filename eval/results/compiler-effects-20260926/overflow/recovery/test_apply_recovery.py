import json
import gzip
import sqlite3

import pytest

import apply_recovery
from apply_recovery import (assert_archived_delta, assert_job_set,
                            assert_persisted_metadata, protected_names,
                            require_archived_launch_entry,
                            write_canonical_receipt)
from eval import campaign_workers
from stage_recovery import delta


def test_recovery_rejects_unknown_inflight_job():
    expected = {'draw', 'update', 'init'}
    assert_job_set({'fast_inflight': [{'id': 'draw'}, {'id': 'update'}]}, expected)
    with pytest.raises(RuntimeError, match='unexpected in-flight'):
        assert_job_set({'fast_inflight': [{'id': 'draw'}, {'id': 'stranger'}]}, expected)


def test_canonical_receipt_retry_accepts_only_identical_bytes(tmp_path):
    path = tmp_path / 'result.json'
    payload = {'status': 'evaluated', 'attempt_id': 42,
               'private_lineage': {'attempt_ids': {3: 42}}}
    write_canonical_receipt(path, payload)
    write_canonical_receipt(path, payload)
    assert json.loads(path.read_bytes()) == json.loads(json.dumps(payload))
    with pytest.raises(RuntimeError, match='conflicting canonical receipt'):
        write_canonical_receipt(path, {'status': 'evaluated', 'attempt_id': 43})


def test_recovery_protects_function_exact_pending_integration_membership():
    nodes = {'object': {'status': 'object_exact'},
             'integrated': {'status': 'integrated'},
             'function': {'status': 'function_exact_pending_integration'},
             'pending': {'status': 'pending'}}
    assert protected_names({'nodes': nodes}) == {'object', 'integrated', 'function'}


def test_private_delta_check_detects_row_content_drift(tmp_path):
    database = tmp_path / 'worker.sqlite'
    with sqlite3.connect(database) as conn:
        conn.executescript('''
            CREATE TABLE attempts (id INTEGER, run_id TEXT, source_code TEXT);
            CREATE TABLE model_proposals (id INTEGER, run_id TEXT);
            CREATE TABLE attempt_edges (child_attempt_id INTEGER, parent_attempt_id INTEGER);
            CREATE TABLE attempt_runs (id TEXT, kind TEXT);
            INSERT INTO attempts VALUES (2, 'run', 'original');
            INSERT INTO attempt_edges VALUES (2, 1);
            INSERT INTO attempt_runs VALUES ('run', 'test');
        ''')
    job = {'id': 'test', 'db': str(database),
           'cutoffs': {'attempts': 1, 'model_proposals': 0}}
    archive = tmp_path / 'delta.json.gz'
    with gzip.open(archive, 'wt') as stream:
        json.dump(delta(job), stream)
    assert_archived_delta(job, archive)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE attempts SET source_code='tampered' WHERE id=2")
    with pytest.raises(RuntimeError, match='private database rows differ'):
        assert_archived_delta(job, archive)


def test_import_attestation_compares_remapped_main_rows(tmp_path, monkeypatch):
    schema = '''
        CREATE TABLE attempts (id INTEGER PRIMARY KEY, run_id TEXT,
                               source_code TEXT, source_sha256 TEXT,
                               sampling TEXT, parent_attempt_id INTEGER);
        CREATE TABLE model_proposals (id INTEGER PRIMARY KEY, run_id TEXT, sampling TEXT);
        CREATE TABLE attempt_edges (child_attempt_id INTEGER, parent_attempt_id INTEGER);
        CREATE TABLE attempt_runs (id TEXT PRIMARY KEY, kind TEXT);
    '''
    private = tmp_path / 'private.sqlite'
    main = tmp_path / 'campaign.sqlite'
    for path in (private, main):
        with sqlite3.connect(path) as conn:
            conn.executescript(schema)
    import hashlib
    source = 'int f(void) { return 1; }'
    with sqlite3.connect(private) as conn:
        conn.execute('INSERT INTO attempt_runs VALUES (?,?)', ('run', 'test'))
        conn.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                     (2, 'run', source, hashlib.sha256(source.encode()).hexdigest(),
                      json.dumps({'parent_attempt_id': 1}), 1))
        conn.execute('INSERT INTO attempt_edges VALUES (?,?)', (2, 1))
    job = {'id': 'test', 'db': str(private),
           'cutoffs': {'attempts': 1, 'model_proposals': 0}}
    archive = tmp_path / 'test.private-delta.json.gz'
    with gzip.open(archive, 'wt') as stream:
        json.dump(delta(job), stream)
    monkeypatch.setattr(apply_recovery, 'STAGE', tmp_path)
    monkeypatch.setattr(apply_recovery, 'NATIVE', tmp_path)
    amap, pmap = campaign_workers.merge(main, private, job['cutoffs'], job['id'])
    apply_recovery.attest_main_rows(job, amap, pmap, campaign_workers)
    with sqlite3.connect(main) as conn:
        conn.execute("UPDATE attempts SET source_code='tampered' WHERE id=?", (amap[2],))
    with pytest.raises(RuntimeError, match='imported attempt differs'):
        apply_recovery.attest_main_rows(job, amap, pmap, campaign_workers)


def test_checkpoint_metadata_roundtrip_normalizes_integer_keys_and_tuples():
    state = {'fast_recovery_events': [{'attempt_ids': {17: 31},
                                       'source': ('old', 'pin')}]}
    persisted = {'fast_recovery_events': [{'attempt_ids': {'17': 31},
                                           'source': ['old', 'pin']}]}
    assert_persisted_metadata(persisted, state, ('fast_recovery_events',))
    persisted['fast_recovery_events'][0]['attempt_ids']['17'] = 32
    with pytest.raises(RuntimeError, match='checkpoint metadata round-trip'):
        assert_persisted_metadata(persisted, state, ('fast_recovery_events',))


def test_launch_entry_may_be_stale_only_if_it_matches_archived_launch():
    key = '/frozen/solver/mips_differential.py'
    archived = {'code_hashes': {key: 'stale-launch-hash'}}
    current = {'code_hashes': {key: 'stale-launch-hash'}}
    assert require_archived_launch_entry(current, archived, key) == 'stale-launch-hash'
    current['code_hashes'][key] = 'unexpected-change'
    with pytest.raises(RuntimeError, match='launch solver entry changed'):
        require_archived_launch_entry(current, archived, key)

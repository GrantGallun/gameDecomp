"""Real interrupted SQLite transactions, not mocked journal/error strings."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from eval import campaign_state as checkpoint


def make_checkpoint(tmp_path):
    path = tmp_path / 'campaign.json'
    value = {'status': 'paused_budget', 'nodes': {'f': {'status': 'object_exact', 'source_sha256': 'source'}},
             'fast_inflight': [{'id': 'preserve-result', 'raw': 'retained.private.json'}], 'config': {}}
    store = checkpoint.Store(path)
    store.save(value)
    with closing(sqlite3.connect(store.db)) as conn, conn:
        conn.execute('CREATE TABLE padding(id INTEGER PRIMARY KEY, payload BLOB)')
        conn.executemany('INSERT INTO padding(payload) VALUES (zeroblob(4096))', [()] * 128)
    return path, store.db, value


def crash_writer(db):
    code = '''import os,sqlite3,sys
conn=sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=DELETE')
conn.execute('PRAGMA synchronous=FULL')
conn.execute('PRAGMA cache_size=2')
conn.execute('PRAGMA cache_spill=ON')
conn.execute('BEGIN IMMEDIATE')
conn.execute("UPDATE commits SET manifest='uncommitted wrong manifest'")
conn.execute('UPDATE padding SET payload=randomblob(4096)')
os._exit(23)
'''
    child = subprocess.run([sys.executable, '-c', code, str(db)], capture_output=True, timeout=30)
    assert child.returncode == 23, child.stderr.decode()
    journal = Path(str(db) + '-journal')
    assert journal.is_file() and journal.stat().st_size > 512
    return journal


def test_real_hot_journal_readonly_fails_controller_recovers_same_commit(tmp_path):
    from eval.completion_campaign import campaign_lock
    path, db, expected = make_checkpoint(tmp_path)
    original = path.read_bytes()
    journal = crash_writer(db)
    journal_hash = hashlib.sha256(journal.read_bytes()).hexdigest()
    with pytest.raises(sqlite3.OperationalError) as captured:
        checkpoint.read(path)
    assert captured.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK
    assert hashlib.sha256(journal.read_bytes()).hexdigest() == journal_hash
    with campaign_lock(path.with_suffix('.lock')):
        assert checkpoint.read_for_resume(path) == expected
    assert path.read_bytes() == original
    assert checkpoint.read(path) == expected
    receipts = list((tmp_path / 'campaign-recovery').glob('*.json'))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_bytes())
    assert receipt['checkpoint'] == json.loads(original)['commit']
    assert receipt['sqlite_errorcode'] == sqlite3.SQLITE_READONLY_ROLLBACK
    assert receipt['selected_objects_verified'] and receipt['integrity_check'] == 'ok'
    # Recovery preserves pending receipts and permits the next durable save.
    with campaign_lock(path.with_suffix('.lock')):
        checkpoint.Store(path).save(expected)
    assert checkpoint.read(path) == expected


def test_recovery_does_not_accept_committed_manifest_corruption(tmp_path):
    from eval.completion_campaign import campaign_lock
    path, db, _ = make_checkpoint(tmp_path)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE commits SET manifest='committed corruption'")
    crash_writer(db)
    original = path.read_bytes()
    with campaign_lock(path.with_suffix('.lock')):
        with pytest.raises(ValueError, match='commit missing or corrupt'):
            checkpoint.read_for_resume(path)
    assert path.read_bytes() == original
    assert not (tmp_path / 'campaign-recovery').exists()


def test_healthy_resume_never_opens_rw_or_runs_integrity_scan(tmp_path, monkeypatch):
    path, _, expected = make_checkpoint(tmp_path)
    connect = checkpoint.sqlite3.connect
    connections = []
    def tracked(database_uri, **kwargs):
        assert str(database_uri).endswith('?mode=ro')
        conn = connect(database_uri, **kwargs)
        connections.append(conn)
        return conn
    monkeypatch.setattr(checkpoint.sqlite3, 'connect', tracked)
    assert checkpoint.read_for_resume(path) == expected
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            conn.execute('SELECT 1')
    assert not (tmp_path / 'campaign-recovery').exists()


def test_generic_io_error_is_not_retried_or_reclassified(tmp_path, monkeypatch):
    path, _, _ = make_checkpoint(tmp_path)
    error = sqlite3.OperationalError('disk I/O error')
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR_WRITE
    error.sqlite_errorname = 'SQLITE_IOERR_WRITE'
    def fail(_):
        raise error
    monkeypatch.setattr(checkpoint, 'read', fail)
    with pytest.raises(sqlite3.OperationalError) as captured:
        checkpoint.read_for_resume(path)
    assert captured.value is error
    assert not (tmp_path / 'campaign-recovery').exists()


def test_resume_rejects_external_store_and_does_not_create_missing_db(tmp_path):
    path = tmp_path / 'campaign.json'
    checkpoint.atomic(path, {'kind': checkpoint.KIND, 'store': '../outside.sqlite', 'commit': 1, 'sha256': 'x'})
    with pytest.raises(ValueError, match='unexpected campaign object store'):
        checkpoint.read_for_resume(path)
    checkpoint.atomic(path, {'kind': checkpoint.KIND, 'store': 'campaign.state.sqlite', 'commit': 1, 'sha256': 'x'})
    with pytest.raises(sqlite3.OperationalError):
        checkpoint.read_for_resume(path)
    assert not path.with_suffix('.state.sqlite').exists()


def test_save_preserves_extended_error_and_attaches_diagnostics(tmp_path, monkeypatch):
    store = checkpoint.Store(tmp_path / 'campaign.json')
    error = sqlite3.OperationalError('disk I/O error')
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR_WRITE
    error.sqlite_errorname = 'SQLITE_IOERR_WRITE'
    calls = []
    def fail(*args):
        calls.append(1)
        raise error
    monkeypatch.setattr(store, '_save', fail)
    with pytest.raises(sqlite3.OperationalError) as captured:
        store.save({'nodes': {}})
    assert captured.value is error and calls == [1]
    diagnostic = json.loads(error.__notes__[-1])
    assert diagnostic['sqlite_errorname'] == 'SQLITE_IOERR_WRITE'
    assert diagnostic['database'] == str(store.db)


def test_service_compact_summary_never_requires_database_even_without_health(tmp_path):
    from eval import campaign_service
    path = tmp_path / 'campaign.json'
    checkpoint.atomic(path, {'kind': checkpoint.KIND, 'store': 'missing.sqlite',
                            'commit': 7, 'status': 'paused_budget'})
    result = campaign_service.read(path, summary_only=True)
    assert result['status'] == 'paused_budget'
    assert result['_checkpoint_health']['checkpoint_health_unavailable']
    with pytest.raises(sqlite3.OperationalError):
        campaign_service.read(path)


def test_fast_controller_holds_campaign_lock_before_recovery_read(tmp_path, monkeypatch):
    import fcntl
    from types import SimpleNamespace
    from eval import fast_campaign
    class ReachedRecovery(Exception):
        pass
    path = tmp_path / 'campaign.json'
    def inspect_lock(selected):
        assert selected == path
        with path.with_suffix('.lock').open('a+b') as handle:
            with pytest.raises(BlockingIOError):
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raise ReachedRecovery
    monkeypatch.setattr(checkpoint, 'read_for_resume', inspect_lock)
    args = SimpleNamespace(resume=True, scheduler='evidence-v1', workers=1, state=path)
    with pytest.raises(ReachedRecovery):
        fast_campaign.run(args)


@pytest.mark.parametrize('active', [False, True])
def test_explicit_resume_resets_failure_budget_only_for_inactive_paused_service(tmp_path, monkeypatch, active):
    from eval import campaign_service as service
    checkpoint.atomic(tmp_path / 'service.json', {'status': 'paused', 'consecutive_failures': 3, 'error': 'old crash'})
    monkeypatch.setattr(service, 'locked', lambda _: active)
    monkeypatch.setattr(service, 'health', lambda _: {'status': 'running' if active else 'paused'})
    monkeypatch.setattr(sys, 'argv', ['campaign_service', '--run', str(tmp_path), 'resume'])
    service.main()
    record = service.read(tmp_path / 'service.json')
    assert record['consecutive_failures'] == (3 if active else 0)
    assert ('error' in record) is active

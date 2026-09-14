"""Normal SQLite hot-journal recovery while supervisor/controller are paused.

No checkpoint selection changes, application writes, journal deletion or DB copy.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time

from eval import campaign_state, completion_campaign

root = Path('/mnt/c/Code/gameDecomp')
run = root / 'eval/results/resume-pipeline-20260908'
out = Path(__file__).resolve().parent / ('recovery-' + str(time.time_ns()))
out.mkdir()
report = {'started_at': time.time(), 'scope': 'SQLite native rollback only; no application checkpoint mutation',
          'output': str(out)}
try:
    assert (run/'service.pause').exists()
    assert json.loads((run/'service-control.json').read_bytes()).get('paused') is True
    with completion_campaign.campaign_lock(run/'resume-supervisor.lock'), completion_campaign.campaign_lock(run/'campaign.lock'):
        for name in ('campaign.json', 'service.json', 'service-control.json', 'launch.json'):
            shutil.copyfile(run/name, out/name)
        pointer_bytes = (run/'campaign.json').read_bytes()
        pointer = json.loads(pointer_bytes)
        db = run/pointer['store']
        assert db.resolve().parent == run.resolve() and db.is_file()
        journal = Path(str(db)+'-journal')
        report['pointer_sha256'] = hashlib.sha256(pointer_bytes).hexdigest()
        report['checkpoint'] = pointer['commit']
        report['database_bytes_before'] = db.stat().st_size
        report['sqlite_version'] = sqlite3.sqlite_version
        report['processes'] = []
        service = json.loads((run/'service.json').read_bytes())
        report['service_batch_work_items'] = service.get('batch_work_items')
        process_ids = {service.get('pid'), service.get('worker_pid'),
                       pointer.get('health', {}).get('performance', {}).get('session_pid')}
        for pid in sorted(p for p in process_ids if isinstance(p, int)):
            cmdline = Path('/proc')/str(pid)/'cmdline'
            report['processes'].append({'pid': pid, 'exists': cmdline.exists(),
                                        'command': cmdline.read_bytes().replace(b'\x00', b' ').decode(errors='replace') if cmdline.exists() else None})
        report['filesystem'] = {}
        for directory in (run, Path('/tmp'), Path('/home/grant/decomp')):
            stat = os.statvfs(directory)
            report['filesystem'][str(directory)] = {'free_bytes': stat.f_bavail*stat.f_frsize,
                                                     'free_inodes': stat.f_favail}
        if journal.exists():
            raw = journal.read_bytes()
            (out/'campaign.state.sqlite-journal.before').write_bytes(raw)
            report['journal_before'] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                        'header_hex': raw[:32].hex(), 'mtime': journal.stat().st_mtime}
        with (run/'pipeline.log').open('rb') as stream:
            stream.seek(max(0, (run/'pipeline.log').stat().st_size-65536))
            (out/'pipeline-tail-before.log').write_bytes(stream.read())
        (out/'before.json').write_text(json.dumps(report, indent=2)+'\n')
        # A schema/page read on a normal read-write connection performs SQLite's
        # own rollback before exposing data. No SQL mutation is issued.
        with sqlite3.connect(f'file:{db.as_posix()}?mode=rw', uri=True, timeout=30) as conn:
            manifest = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()
            assert manifest and hashlib.sha256(manifest[0]).hexdigest() == pointer['sha256']
            report['manifest_sha256_verified'] = True
            report['page_count'] = conn.execute('PRAGMA page_count').fetchone()[0]
            report['freelist_count'] = conn.execute('PRAGMA freelist_count').fetchone()[0]
        state = campaign_state.read(run/'campaign.json')
        report['selected_checkpoint_all_object_hashes_verified'] = True
        report['functions'] = len(state['nodes'])
        report['status'] = state['status']
        report['status_counts'] = dict(Counter(n['status'] for n in state['nodes'].values()))
        report['inflight'] = state.get('inflight')
        report['fast_inflight'] = [{k: j.get(k) for k in ('id', 'function', 'raw', 'receipt', 'slot')}
                                   for j in state.get('fast_inflight', [])]
        for job in report['fast_inflight']:
            raw = Path(job['raw'])
            job['raw_exists'] = raw.is_file()
            if raw.is_file():
                job['raw_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
        report['journal_exists_after'] = journal.exists()
        report['database_bytes_after'] = db.stat().st_size
        report['pointer_unchanged'] = (run/'campaign.json').read_bytes() == pointer_bytes
        assert report['pointer_unchanged']
        report['paused_after'] = (run/'service.pause').exists()
        report['result'] = 'recovered_and_hash_verified'
except Exception as exc:
    report.update(result='error', error=f'{type(exc).__name__}: {exc}')
    raise
finally:
    report['finished_at'] = time.time()
    (out/'recovery.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)

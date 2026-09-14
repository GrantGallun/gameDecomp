"""Verify existing inputs and resumable jobs after the observed import lock failure."""
import hashlib
import json
import sqlite3
import sys
import time
import zlib
from pathlib import Path

run = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
sys.path.insert(0, str(run / 'code'))
from eval import completion_campaign as campaign

with campaign.campaign_lock(run / 'campaign.lock'):
    pointer = json.loads((run / 'campaign.json').read_text())
    with sqlite3.connect(f'file:{run / pointer["store"]}?mode=ro', uri=True) as conn:
        raw = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0]
        assert hashlib.sha256(raw).hexdigest() == pointer['sha256']
        manifest = json.loads(raw)
        compressed = conn.execute('SELECT payload FROM objects WHERE hash=?', (manifest['metadata'],)).fetchone()[0]
        metadata_raw = zlib.decompress(compressed)
        assert hashlib.sha256(metadata_raw).hexdigest() == manifest['metadata']
        state = json.loads(metadata_raw)
    assert not state.get('inflight'), 'Legacy inflight work needs its original runtime'
    repo = Path('/home/grant/decomp/sbk1')
    pins = campaign._pins(run / 'code', repo)
    pins.update(campaign.frozen_wavefront.file_hashes(
        [Path(p) for p in state['pins'] if Path(p).is_relative_to(repo / 'nonmatchings')]))
    assert pins == state['pins'], 'Frozen inputs changed'
    with sqlite3.connect(run / 'campaign.sqlite', timeout=5) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
        assert campaign.digest(inventory) == state['inventory_sha256']
        conn.execute('BEGIN IMMEDIATE')
        conn.rollback()
    digest = campaign.frozen_wavefront.model_digest('http://172.28.32.1:11435', 'gpt-oss:20b')
    assert digest == state['model_digest']
    jobs = [{k: j.get(k) for k in ('id', 'function', 'private_db', 'raw')}
            for j in state.get('fast_inflight', [])]
    for job in jobs:
        job['raw_exists'] = Path(job['raw']).exists()
    receipt = dict(checked_at=time.time(), checkpoint=pointer['commit'],
                   pins_verified=len(pins), pin_sha256=campaign.digest(pins),
                   model_digest=digest, inventory_verified=True,
                   database_write_lock_available=True, preserved_fast_inflight=jobs,
                   completed_items=pointer['fast_metrics']['completed_items'],
                   runtime_options=state.get('runtime_options'), summary=state.get('summary'),
                   observed_failure='sqlite3.OperationalError: database is locked during campaign_workers.merge')
    Path(__file__).with_name('post-lock-pre-resume.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))

sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from eval import campaign_service
sys.argv = ['campaign_service', '--run', str(run), '--batch', '200', 'resume']
campaign_service.main()
